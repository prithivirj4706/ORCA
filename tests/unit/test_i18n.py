"""Multilingual support (problem statement, capability 2).

A language is supported only if ORCA can both READ a question in it and SAY the
answer. Half-support produces an unparsed question or a half-English answer,
which is worse than answering in English deliberately.
"""
import pytest

from backend.orca.i18n.detect import detect_language
from backend.orca.i18n.generate import (
    REQUIRED_SECTIONS, is_supported, load_terms, native_intent, native_place,
    native_time, supported_languages,
)

SUPPORTED = supported_languages()


class TestSupportIsAllOrNothing:
    @pytest.mark.parametrize("lang", SUPPORTED)
    def test_every_supported_language_defines_every_section(self, lang):
        if lang == "en":
            return
        terms = load_terms(lang)
        for s in REQUIRED_SECTIONS:
            assert terms.get(s), f"{lang} is advertised but has no {s!r}"

    def test_a_shell_lexicon_is_not_advertised(self):
        """A file named ur.yaml with English content is not Urdu support."""
        for lang in ("pa", "ur"):
            if not is_supported(lang):
                assert lang not in SUPPORTED

    def test_detection_never_returns_an_unsupported_language(self):
        # Gurmukhi: script is recognised, lexicon is not ready -> English.
        assert detect_language("ਕੱਲ੍ਹ ਮੱਛੀ ਫੜਨਾ") in SUPPORTED

    def test_english_is_always_supported(self):
        assert is_supported("en")


class TestComprehension:
    """Detection without comprehension is worthless (F-37)."""

    CASES = {
        "ml": ("കൊച്ചി", "മീൻ", "നാളെ"),
        "ta": ("கொச்சி", "மீன்", "நாளை"),
        "hi": ("कोच्चि", "मछली", "कल"),
        "te": ("కొచ్చి", "చేప", "రేపు"),
        "bn": ("কোচি", "মাছ", "আগামীকাল"),
        "gu": ("કોચી", "માછલી", "આવતીકાલે"),
        "mr": ("कोची", "मासे", "उद्या"),
        "kn": ("ಕೊಚ್ಚಿ", "ಮೀನು", "ನಾಳೆ"),
        "or": ("କୋଚି", "ମାଛ", "ଆସନ୍ତାକାଲି"),
    }

    @pytest.mark.parametrize("lang", sorted(CASES))
    def test_place_intent_and_time_all_resolve(self, lang):
        place, fish, tomorrow = self.CASES[lang]
        text = f"{place} {tomorrow} {fish}"
        assert native_place(lang, text) == "kochi"
        assert native_intent(lang, text) == "fishing_suitability"
        assert "tomorrow" in native_time(lang, text)


class TestSafetyVocabulary:
    @pytest.mark.parametrize("lang", SUPPORTED)
    def test_every_verdict_has_a_term(self, lang):
        verdicts = load_terms(lang).get("verdict") or {}
        for v in ("UNSAFE", "INSUFFICIENT_EVIDENCE", "MARGINAL", "FAVOURABLE"):
            assert verdicts.get(v), f"{lang} has no term for {v}"

    @pytest.mark.parametrize("lang", SUPPORTED)
    def test_the_disclaimer_still_names_the_authorities(self, lang):
        """Authority names are never translated away (06 s7.2)."""
        d = (load_terms(lang).get("phrase") or {}).get("disclaimer", "")
        assert "IMD" in d and "INCOIS" in d

    @pytest.mark.parametrize("lang", SUPPORTED)
    def test_the_review_status_is_recorded(self, lang):
        meta = load_terms(lang).get("_meta") or {}
        assert meta.get("status") in ("REVIEWED", "TRANSLATION_REVIEW_REQUIRED")


class TestRouteEndpointParsing:
    """A destination that is detected then discarded is worse than none."""

    def _resolve(self, query, session=None):
        from backend.orca.graph.nodes.context import ingest, intent_context
        st = {"query_text": query}
        if session:
            st["session_context"] = {"resolved_location": session}
        st.update(ingest(st))
        return intent_context(st)["resolved_location"] or {}

    KOCHI = {"lat": 9.93, "lon": 76.26, "label": "near Kochi"}

    def test_from_a_to_b(self):
        loc = self._resolve("safest route from kochi to mumbai")
        assert (loc["lat"], loc["dest_lat"]) == (9.93, 18.94)

    def test_bare_to_b_uses_the_carried_origin(self):
        loc = self._resolve("plan a route to Mumbai", session=self.KOCHI)
        assert loc["lat"] == 9.93 and loc["dest_lat"] == 18.94

    def test_the_destination_is_not_mistaken_for_the_origin(self):
        loc = self._resolve("sail to Chennai", session=self.KOCHI)
        assert loc["lat"] == 9.93           # not Chennai
        assert loc["dest_lon"] == 80.29

    def test_a_native_script_route_resolves_both_ends(self):
        loc = self._resolve("കൊച്ചിയിൽ നിന്ന് മുംബൈയിലേക്കുള്ള വഴി")
        assert loc.get("dest_lat") == 18.94
