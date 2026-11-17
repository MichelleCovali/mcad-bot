from mcad.models import Incident
from mcad.service import incident_dict


def test_country_facets_resolve_workbook_aliases_but_preserve_current_corrections():
    incident = Incident(title="Original report", incident_country="US", victim_country="Belgium, and Netherlands",
                        attacker_country="Unknown", raw_fields={
                            "_country_relations": {
                                "incident_country": [{"source": "US", "name": "United States"}],
                                "victim_country": [{"source": "Belgium", "name": "Belgium"},
                                                   {"source": "and Netherlands", "name": "Netherlands"}],
                            },
                        })

    original = incident_dict(incident)
    assert original["incidentCountry"] == "US"
    assert original["countryFacets"] == {"incidentCountry": ["United States"], "victimCountry": ["Belgium", "Netherlands"]}
    assert "rawFields" not in original

    incident.incident_country = "Canada"
    corrected = incident_dict(incident)
    assert corrected["incidentCountry"] == "Canada"
    assert "incidentCountry" not in corrected["countryFacets"]
