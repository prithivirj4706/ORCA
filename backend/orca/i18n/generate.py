import os
import yaml

TERMS_DIR = os.path.join(os.path.dirname(__file__), "terms")
_cache = {}

def load_terms(lang: str) -> dict:
    if lang in _cache:
        return _cache[lang]
    path = os.path.join(TERMS_DIR, f"{lang}.yaml")
    if not os.path.exists(path):
        path = os.path.join(TERMS_DIR, "en.yaml")
    with open(path, "r", encoding="utf-8") as f:
        terms = yaml.safe_load(f)
    _cache[lang] = terms
    return terms

def t(lang: str, key: str, default: str = None) -> str:
    terms = load_terms(lang)
    keys = key.split(".")
    val = terms
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            # Fallback to English if missing in local translation
            if lang != "en":
                return t("en", key, default)
            return default or key
    return str(val)

def generate_template(language: str, assessments: list, s) -> str:
    """Grounded by construction: every sentence is built from an assessment.
    Translated safely without modifying numeric data or geometries.
    """
    # The headline is not translated here yet since it's composed upstream, 
    # but we can translate its components if we need to. For now, headline is kept as-is or we can rebuild it.
    # Actually, the spec says "translate the FRAME... never numbers". 
    # For MVP, s.headline is returned as-is (or we can translate it if it's structural).
    # The engine composes the headline in English. When the language defines a
    # headline for this category, use it; otherwise keep the engine's, which is
    # always correct even if untranslated.
    out = [t(language, f"category.{getattr(s, 'category', '')}", s.headline)]
    
    for a in assessments:
        limiting = next((d for d in a.drivers if d.contribution == "limiting"), None)
        
        domain_name = t(language, f"domain.{a.domain.name}", a.domain.value)
        verdict_name = t(language, f"verdict.{a.verdict.name}", a.verdict.value)
        confidence_name = t(language, f"confidence.{a.confidence.name}", a.confidence.value)
        
        bit = t(language, "phrase.assessment_base", "{domain}: {verdict} (confidence {confidence})").format(
            domain=domain_name, verdict=verdict_name, confidence=confidence_name
        )
        
        if limiting is not None:
            if limiting.value is True:
                val = t(language, "value.inside", "inside")
            elif limiting.value is False:
                val = t(language, "value.outside", "outside")
            else:
                val = f"{limiting.value:g} {limiting.unit or ''}".strip()
                
            factor_name = t(language, f"factor.{limiting.factor}", limiting.factor)
            limiting_phrase = t(language, "phrase.limiting_factor", "; limiting factor {factor} at {val}").format(
                factor=factor_name, val=val
            )
            bit += limiting_phrase
            
        if a.missing_required:
            missing_names = ", ".join(t(language, f"factor.{m}", m) for m in a.missing_required)
            bit += t(language, "phrase.missing_required", "; no verdict issued for want of {missing}").format(
                missing=missing_names
            )
            
        if getattr(a, "verdict_capped_by", None):
            capped_names = ", ".join(t(language, f"factor.{c}", c) for c in a.verdict_capped_by)
            bit += t(language, "phrase.verdict_capped_by", "; capped at this level because {capped} could not be checked").format(
                capped=capped_names
            )
            
        out.append(bit + ".")
        
        if a.not_evaluated:
            not_checked = ", ".join(
                f"{t(language, f'factor.{n.factor}', n.factor)} ({t(language, f'reason.{n.reason}', n.reason)})" 
                for n in a.not_evaluated
            )
            out.append(t(language, "phrase.not_checked", "  Not checked: {not_checked}.").format(
                not_checked=not_checked
            ))
            
    disclaimer = t(language, "phrase.disclaimer", "This is an ORCA assessment, not an official advisory. Follow IMD and INCOIS bulletins.")
    out.append(disclaimer)
    return "\n".join(out)


def section(lang: str, name: str) -> dict:
    """A whole lexicon section, empty when the language does not define it.

    Adding a language stays a YAML drop: nothing here knows which languages
    exist, and a missing section degrades to the English/Latin path rather
    than failing.
    """
    try:
        return dict((load_terms(lang) or {}).get(name) or {})
    except Exception:
        return {}


def native_place(lang: str, text: str) -> str | None:
    """Canonical gazetteer key for a place written in the user's own script."""
    for native, key in section(lang, "place").items():
        if native and native in text:
            return key
    return None


def native_intent(lang: str, text: str) -> str | None:
    for intent, words in section(lang, "intent").items():
        if any(w and w in text for w in (words or [])):
            return intent
    return None


def native_time(lang: str, text: str) -> set[str]:
    """Which time expressions the query uses, as English keys."""
    found = set()
    for key, words in section(lang, "time").items():
        if any(w and w in text for w in (words or [])):
            found.add(key)
    return found


#: Sections a language must define before ORCA will answer in it. The first
#: three are COMPREHENSION (can we read the question?), the rest are OUTPUT
#: (can we say the answer?). A lexicon missing any of them is not "partial
#: support" -- it produces a question ORCA cannot parse or an answer half in
#: English, both of which are worse than answering in English on purpose.
REQUIRED_SECTIONS = ("place", "intent", "time",
                     "verdict", "domain", "confidence", "phrase", "category")


def is_supported(lang: str) -> bool:
    if lang == "en":
        return True
    terms = load_terms(lang) or {}
    return all(terms.get(s) for s in REQUIRED_SECTIONS)


def supported_languages() -> list[str]:
    import pathlib
    d = pathlib.Path(__file__).parent / "terms"
    return sorted(p.stem for p in d.glob("*.yaml") if is_supported(p.stem))
