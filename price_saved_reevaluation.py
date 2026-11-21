"""Audited local reevaluation of an already captured price sample."""
from __future__ import annotations

import hashlib
import json
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from filelock import FileLock, Timeout

from competitor_requirements import business_tax_evidence, business_tax_policy_for
from price_comparator import PriceComparatorStore
from price_monitor import _iso
from price_web_extract import ProductPage, resolve_market
from price_web import WebPriceSearch
from price_web_extract import apply_tax_policy
from price_references import EXACT, classify


def _utc(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        at = value
    else:
        at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _sample_digest(case: dict) -> str:
    payload = json.dumps(case["sample"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def commercial_market_url(case: dict) -> str:
    commercial = case.get("commercial_config") or {}
    return commercial.get("market_evidence_url") or case["url"]


def commercial_market_note(case: dict) -> str:
    commercial = case.get("commercial_config") or {}
    return commercial.get("market_note") or "Configuración comercial documentada sin inferencia de dominio"


class SavedSampleReevaluation:
    """No HTTP. Reuses an exact exported sample and writes only through existing comparator tables."""

    def __init__(self, store: PriceComparatorStore, evidence: str | Path, *, clock=None):
        self.store = store
        self.evidence = Path(evidence)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _case(self, request_id: int) -> dict:
        if self.evidence.suffix.lower() == ".zip":
            import zipfile
            with zipfile.ZipFile(self.evidence) as archive:
                data = json.loads(archive.read("revision-7-casos-original.json").decode("utf-8-sig"))
        else:
            data = json.loads(self.evidence.read_text(encoding="utf-8-sig"))
        for case in data.get("cases", []):
            if case.get("id") == request_id:
                return case
        raise ValueError("Solicitud manual no existe en el paquete histórico")

    def preview(self, request_id: int) -> dict:
        case = self._case(request_id)
        now = _utc(self.clock())
        sample = case.get("sample")
        if not isinstance(sample, dict) or not isinstance(sample.get("page"), dict):
            return self._result(case, "needs_evidence", "La solicitud no conserva una muestra utilizable")
        at = _utc(sample["at"])
        interval = 24
        expires = at + timedelta(hours=interval)
        if expires <= now:
            return self._result(case, "expired", "Muestra caducada; no se renueva ni se importa",
                                expires=expires)

        page = sample["page"]
        if case["requested_at"] and at <= _utc(case["requested_at"]):
            return self._result(case, "contradiction", "La muestra es anterior o igual a la solicitud",
                                expires=expires)
        if page.get("ean_published") != case["ean"] or page.get("ambiguous"):
            return self._result(case, "identity", "EAN o identidad ambigua")
        if page.get("url") != case["url"] or page.get("offer_binding_unresolved"):
            return self._result(case, "identity", "URL o vinculación de oferta no acreditadas")
        binding = page.get("offer_binding_source")
        if binding not in {"product_offer", "product_group_offer", "variant_url"}:
            return self._result(case, "identity", "La oferta no tiene un vínculo verificable con el producto")
        if binding in {"product_group_offer", "variant_url"} and not page.get("variant_scoped"):
            return self._result(case, "identity", "La oferta no está vinculada a la variante seleccionada")
        if page.get("availability") not in {"available", "unavailable"}:
            return self._result(case, "evidence", "Disponibilidad no acreditada")
        if page.get("currency") != "EUR" or page.get("price") is None:
            return self._result(case, "commercial", "Precio o moneda no acreditados")

        commercial = case.get("commercial_config") or {}
        from dataclasses import fields
        page_obj = ProductPage(**{field.name: page[field.name] for field in fields(ProductPage) if field.name in page})
        market, market_reason, market_conflict = resolve_market(page_obj, commercial)
        if market_conflict:
            return self._result(case, "contradiction", market_reason, expires=expires, current_market=market)
        if market and market != "ES":
            return self._result(case, "commercial", f"Mercado observado {market}; se requiere ES",
                                expires=expires, current_market=market)
        if not commercial.get("market_verified") or commercial.get("sales_market") != "ES":
            return self._result(case, "commercial", "Mercado ES no confirmado para esta reevaluación",
                                expires=expires, current_market=market)
        policy = "included"
        if page.get("includes_tax") is False:
            return self._result(case, "contradiction", "IVA excluido contradice la política vigente")
        if page.get("includes_tax") is None:
            policy = commercial.get("tax_policy", "included")
            if policy != "included":
                return self._result(case, "commercial", "Política fiscal vigente no confirmada")

        return self._result(case, "eligible", "Muestra vigente, identidad exacta y configuración ES/IVA incluido",
                            expires=expires, current_market=market or "ES")

    def _result(self, case: dict, category: str, reason: str, *, expires=None, current_market=None) -> dict:
        sample = case.get("sample") or {}
        page = sample.get("page") or {}
        commercial = case.get("commercial_config") or {}
        return {
            "request_id": case["id"], "ean": case["ean"], "competitor_id": case["competitor_id"],
            "url": case["url"], "category": category, "reason": reason,
            "sampled_at": case.get("sampled_at"),
            "expires_at": expires.isoformat() if expires else None,
            "previous_gap": (sample.get("gap") or ""),
            "previous_tax_policy": page.get("configured_tax_policy", "unknown"),
            "current_market": current_market if current_market is not None
            else page.get("market") or commercial.get("sales_market", ""),
            "current_tax_policy": "included",
            "http_required": False, "sample_digest": _sample_digest(case),
        }

    def apply(self, request_id: int, *, actor: str, note: str, build_commit: str,
              approved: bool = False, now=None) -> dict:
        if not approved or not actor.strip() or not note.strip():
            raise ValueError("Confirmación, responsable y motivo son obligatorios")
        if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
            raise ValueError("Commit de 40 caracteres requerido")
        at = _utc(now or self.clock())
        preview = self.preview(request_id)
        if preview["category"] != "eligible":
            raise ValueError(preview["reason"])
        case = self._case(request_id)
        page = case["sample"]["page"]
        digest = preview["sample_digest"]

        path = Path(self.store.database_path).resolve()
        with FileLock(f"{path}.saved-reevaluation.lock", timeout=0):
            with self.store._connect() as con, con:
                job = con.execute("""SELECT * FROM monitor_jobs
                    WHERE competitor_id=? AND ean=?""",
                                  (case["competitor_id"], case["ean"])).fetchone()
                if not job:
                    raise ValueError("No existe el trabajo monitorizado de la muestra")
                persisted_sample = job["sample"] or job["previous_sample"]
                try:
                    persisted = json.loads(persisted_sample)
                except (TypeError, json.JSONDecodeError):
                    persisted = None
                if (not isinstance(persisted, dict)
                        or _sample_digest({"sample": persisted}) != digest):
                    raise ValueError("La muestra persistida no coincide con el paquete histórico")

                previous_audit = con.execute("""SELECT id FROM access_audit
                    WHERE scope='manual_review' AND scope_id=? AND action='reevaluate_saved_sample'
                    LIMIT 1""", (str(request_id),)).fetchone()
                if previous_audit:
                    return {**preview, "applied": False, "reason": "Ya reevaluada; repetición idempotente"}
                expires = datetime.fromisoformat(preview["expires_at"]).astimezone(timezone.utc)
                from dataclasses import fields
                values = {field.name: page.get(field.name) for field in fields(ProductPage)
                          if field.name in page}
                page_obj = ProductPage(**values)
                product = con.execute("SELECT * FROM products WHERE ean=?", (case["ean"],)).fetchone()
                con.execute("INSERT OR IGNORE INTO competitor_web_config(competitor_id)"
                            " SELECT id FROM competitors WHERE id=?", (case["competitor_id"],))
                config = con.execute("""SELECT w.*,c.name,c.url AS site_url,c.domain,c.market,c.currency
                    FROM competitor_web_config w JOIN competitors c ON c.id=w.competitor_id
                    WHERE w.competitor_id=?""", (case["competitor_id"],)).fetchone()
                if not product or not config:
                    raise ValueError("Producto o configuración comercial no disponible")
                config = dict(config)
                market, market_reason, market_conflict = resolve_market(page_obj, config)
                if market_conflict or market != "ES":
                    raise ValueError(market_reason or f"Mercado actual no compatible: {market or 'sin confirmar'}")
                apply_tax_policy(page_obj, config["tax_policy"])
                if page_obj.includes_tax is not True:
                    raise ValueError("IVA incluido no está confirmado en la configuración actual")
                monitor_eligible = int(page_obj.availability == "available" and not page_obj.stock_conflict)
                from price_web import Outcome
                outcome = Outcome(case["ean"], case["competitor_id"], config["name"], "exact",
                                 url=case["url"], page=page_obj, match_status=EXACT,
                                 method="historical:local-reevaluation")
                search = WebPriceSearch(self.store)
                frame = pd.DataFrame([search.frame_row(dict(product), config, outcome, case["sampled_at"])])

                def validate(transaction):
                    transaction.execute("""UPDATE monitor_jobs SET detail=?,eligible=?,last_ok_at=?,valid_until=?,updated_at=?
                        WHERE id=?""",
                               ("Reevaluación local: ES/IVA incluido; muestra original conservada"
                                if monitor_eligible else
                                "Reevaluación local: sin stock; precio conservado, no comparable",
                                monitor_eligible, case["sampled_at"], expires.isoformat(), at.isoformat(), job["id"]))
                    transaction.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                        VALUES('manual_review',?,'reevaluate_saved_sample',?,?,?,?,?)""",
                               (str(request_id),
                                json.dumps({"gap": case["sample"].get("gap"),
                                            "tax_policy": page["configured_tax_policy"],
                                            "sampled_at": case["sampled_at"], "eligible": job["eligible"]},
                                           ensure_ascii=False),
                                json.dumps({"market": "ES", "tax_policy": "included",
                                            "sampled_at": case["sampled_at"], "expires_at": expires.isoformat(),
                                            "sample_digest": digest, "eligible": monitor_eligible,
                                            "availability": page_obj.availability, "offer_imported": True},
                                           ensure_ascii=False),
                                actor.strip(), json.dumps({"reason": note.strip(), "build_commit": build_commit,
                                                          "http_requests": 0}, ensure_ascii=False),
                                at.isoformat()))

                report = self.store.import_offers(frame, validate=validate, connection=con)
                if report.rejected:
                    raise ValueError(report.rejected[0])
        return {**preview, "applied": True}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--request-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--actor", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)

    service = SavedSampleReevaluation(PriceComparatorStore(args.db), args.evidence)
    if args.apply:
        result = service.apply(args.request_id, actor=args.actor, note=args.note,
                               build_commit=args.commit, approved=args.approved)
    else:
        result = service.preview(args.request_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

