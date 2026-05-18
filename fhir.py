"""
Adaptadores HL7 FHIR R4 ↔ schema interno.
Implementa los resources mínimos: Patient, Observation (SV), Encounter (turno).

Estándar: https://www.hl7.org/fhir/R4/

Esta capa es la base para integración con SEIS o cualquier HIS FHIR-native.
NO implementa server SMART-on-FHIR (auth OAuth2). Para producción agregar
authlib + SMART scopes.
"""
from datetime import datetime
from typing import Optional


# === FHIR → schema interno ===

def fhir_patient_to_paciente(resource: dict) -> dict:
    """Convierte un Patient FHIR a dict para INSERT en `pacientes`."""
    if resource.get("resourceType") != "Patient":
        raise ValueError("resource no es Patient")

    # Nombre
    name = ""
    if resource.get("name"):
        n = resource["name"][0]
        given = " ".join(n.get("given", []))
        family = n.get("family", "")
        name = f"{given} {family}".strip()

    # RUT desde identifier system="https://salud.gob.cl/rut"
    rut = None
    for ident in resource.get("identifier", []):
        if ident.get("system", "").endswith("/rut"):
            rut = ident.get("value")
            break

    # Edad desde birthDate
    edad = None
    bd = resource.get("birthDate")
    if bd:
        try:
            born = datetime.strptime(bd, "%Y-%m-%d")
            edad = (datetime.now() - born).days // 365
        except ValueError:
            pass

    # Sexo
    gender = resource.get("gender", "")
    sexo_map = {"male": "M", "female": "F", "other": "O", "unknown": None}
    sexo = sexo_map.get(gender)

    return {
        "nombre": name or "(sin nombre)",
        "rut": rut,
        "edad": edad,
        "sexo": sexo,
    }


def fhir_observation_to_sv(resource: dict) -> dict:
    """Convierte una Observation FHIR (signo vital) a entrada en signos_vitales."""
    if resource.get("resourceType") != "Observation":
        raise ValueError("resource no es Observation")
    out: dict = {}
    # Múltiples observations en una sola (component)
    components = resource.get("component", [])
    if not components:
        components = [resource]

    LOINC_MAP = {
        "8480-6": "pa_sis",   # PA sistólica
        "8462-4": "pa_dia",   # PA diastólica
        "8867-4": "fc",       # FC
        "9279-1": "fr",       # FR
        "8310-5": "temp",     # Temp
        "59408-5": "sato2",   # SpO2
        "9269-2": "glasgow",  # GCS
        "33747-0": "hgt",     # Glucosa capilar
    }
    pa_sis = pa_dia = None
    for c in components:
        codings = c.get("code", {}).get("coding", [])
        valor = (c.get("valueQuantity") or {}).get("value")
        for cod in codings:
            campo = LOINC_MAP.get(cod.get("code"))
            if campo and valor is not None:
                if campo == "pa_sis": pa_sis = int(valor)
                elif campo == "pa_dia": pa_dia = int(valor)
                else: out[campo] = valor
    if pa_sis and pa_dia:
        out["pa"] = f"{pa_sis}/{pa_dia}"
    return out


# === schema interno → FHIR ===

def paciente_to_fhir(p: dict, hospital_codigo: Optional[str] = None) -> dict:
    """Convierte un paciente del schema a FHIR Patient R4."""
    given = p["nombre"].split()
    family = given[-1] if len(given) > 1 else ""
    given = given[:-1] if len(given) > 1 else given
    resource = {
        "resourceType": "Patient",
        "id": str(p["id"]),
        "name": [{
            "use": "official",
            "given": given,
            "family": family,
            "text": p["nombre"],
        }],
    }
    if p.get("rut"):
        resource["identifier"] = [{
            "system": "https://salud.gob.cl/rut",
            "value": p["rut"],
        }]
    if p.get("sexo"):
        resource["gender"] = {"M": "male", "F": "female"}.get(p["sexo"], "other")
    if p.get("edad"):
        approx_year = datetime.now().year - p["edad"]
        resource["birthDate"] = f"{approx_year}-01-01"
    return resource


def sv_to_fhir_observation(sv: dict, paciente_id: int) -> dict:
    """Genera un FHIR Observation con multi-component para los SV."""
    components = []
    if sv.get("pa"):
        try:
            sis, dia = sv["pa"].split("/")
            components.append({
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                "valueQuantity": {"value": int(sis), "unit": "mmHg"},
            })
            components.append({
                "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}]},
                "valueQuantity": {"value": int(dia), "unit": "mmHg"},
            })
        except (ValueError, IndexError):
            pass

    INV_LOINC = {
        "fc": ("8867-4", "/min"),
        "fr": ("9279-1", "/min"),
        "temp": ("8310-5", "Cel"),
        "sato2": ("59408-5", "%"),
        "glasgow": ("9269-2", "{score}"),
        "hgt": ("33747-0", "mg/dL"),
    }
    for k, (loinc, unit) in INV_LOINC.items():
        if sv.get(k) is not None:
            components.append({
                "code": {"coding": [{"system": "http://loinc.org", "code": loinc}]},
                "valueQuantity": {"value": sv[k], "unit": unit},
            })

    return {
        "resourceType": "Observation",
        "id": str(sv.get("id", "")),
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "vital-signs",
        }]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "85353-1",
                             "display": "Vital signs panel"}]},
        "subject": {"reference": f"Patient/{paciente_id}"},
        "effectiveDateTime": sv.get("creado_en"),
        "component": components,
    }
