import re

def detect_language(text: str) -> str:
    """Detect language from Unicode script ranges. Deterministic."""
    if not text:
        return "en"
    
    # Unicode block ranges for popular Indian scripts
    ranges = {
        "ml": r'[\u0D00-\u0D7F]', # Malayalam
        "hi": r'[\u0900-\u097F]', # Devanagari (Hindi, Marathi)
        "ta": r'[\u0B80-\u0BFF]', # Tamil
        "te": r'[\u0C00-\u0C7F]', # Telugu
        "bn": r'[\u0980-\u09FF]', # Bengali
        "gu": r'[\u0A80-\u0AFF]', # Gujarati
        "kn": r'[\u0C80-\u0CFF]', # Kannada
        "or": r'[\u0B00-\u0B7F]', # Odia
        "pa": r'[\u0A00-\u0A7F]', # Gurmukhi (Punjabi)
        "ur": r'[\u0600-\u06FF]', # Arabic (Urdu)
    }
    from .generate import is_supported

    for lang, pattern in ranges.items():
        if re.search(pattern, text):
            # Detecting a script ORCA cannot actually serve is worse than not
            # detecting it: the question would go unparsed and the answer would
            # come back half-translated. Fall back to English deliberately.
            return lang if is_supported(lang) else "en"
    return "en"
