"""
Single Vercel Python entrypoint for the whole backend.

Vercel's current Python runtime looks for exactly one entrypoint --
app.py / index.py / server.py / main.py / wsgi.py / asgi.py -- at the
project root or inside src/, app/, or api/, exporting a top-level `app`
object (Flask/FastAPI/WSGI/ASGI). It deploys that as a single Vercel
Function and routes every request to it. It does NOT auto-create one
function per file the way the older runtime did.

Everything -- questionnaire data, domain scoring, the rule-based
classifier, optional LLM feature extraction, and serving the static
frontend -- lives in this one file so there is nothing left to import
across a folder boundary and nothing for Vercel to misdetect.

RESEARCH PROTOTYPE. Outputs are screening classifications, never a
clinical diagnosis. See DISCLAIMER below and the project README.
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Questionnaire data (single source of truth)
# ---------------------------------------------------------------------------

FREQUENCY_SCALE = [
    {"value": 0, "label": "Never"},
    {"value": 1, "label": "Rarely"},
    {"value": 2, "label": "Sometimes"},
    {"value": 3, "label": "Often"},
    {"value": 4, "label": "Very often"},
]

CHANGE_SCALE = [
    {"value": 0, "label": "No change"},
    {"value": 1, "label": "Slight change"},
    {"value": 2, "label": "Noticeable change"},
    {"value": 3, "label": "Significant change"},
    {"value": 4, "label": "Severe change"},
]

INDEPENDENCE_SCALE = [
    {"value": 0, "label": "Completely independent"},
    {"value": 1, "label": "Independent, but with minor difficulty"},
    {"value": 2, "label": "Occasionally need assistance"},
    {"value": 3, "label": "Frequently need assistance"},
    {"value": 4, "label": "Unable to perform independently"},
]

SCALES = {
    "frequency": FREQUENCY_SCALE,
    "change": CHANGE_SCALE,
    "independence": INDEPENDENCE_SCALE,
}

SECTIONS = [
    {"id": "attention", "title": "Attention & Focus", "range": (1, 7)},
    {"id": "hyperactivity", "title": "Hyperactivity", "range": (8, 9)},
    {"id": "impulsivity", "title": "Impulsivity", "range": (10, 11)},
    {"id": "childhood_history", "title": "Developmental History", "range": (12, 14)},
    {"id": "memory", "title": "Memory", "range": (15, 19)},
    {"id": "executive", "title": "Executive Function", "range": (20, 22)},
    {"id": "language", "title": "Language", "range": (23, 23)},
    {"id": "orientation", "title": "Orientation", "range": (24, 24)},
    {"id": "cognitive_decline", "title": "Cognitive Change", "range": (25, 27)},
    {"id": "functional_impairment", "title": "Daily Functioning", "range": (28, 30)},
]

_QUESTION_TEXT = {
    1: "How often do you have difficulty maintaining attention during a task, lecture, conversation, or activity?",
    2: "How often do you make mistakes because you overlook details or fail to notice important information?",
    3: "How often do you start tasks but have difficulty finishing them?",
    4: "How often do you have difficulty organizing your work, studies, or daily activities?",
    5: "How often do you avoid or delay tasks that require prolonged mental effort?",
    6: "How often are you easily distracted by unrelated sounds, thoughts, notifications, or activities?",
    7: "How often do you forget appointments, deadlines, instructions, or everyday responsibilities?",
    8: "How often do you feel restless when you are expected to remain seated or still?",
    9: "How often do you feel unable to relax without doing something or keeping yourself occupied?",
    10: "How often do you interrupt conversations or answer before another person has finished speaking?",
    11: "How often do you find it difficult to wait for your turn?",
    12: "Did you experience persistent difficulties with attention, organization, or impulsivity during childhood?",
    13: "During childhood or adolescence, did these difficulties interfere with your schoolwork, relationships, or daily activities?",
    14: "Have your attention-related difficulties been present for a long period rather than appearing only recently?",
    15: "How often do you forget information that you learned recently?",
    16: "How often do you forget conversations or events that happened recently?",
    17: "How often do you repeat a question, story, or information because you do not remember having already mentioned it?",
    18: "How often do you have difficulty learning and remembering new information?",
    19: "How often do you forget where you have placed commonly used objects?",
    20: "How often do you have difficulty planning or carrying out a familiar multi-step task?",
    21: "How often do you have difficulty solving a problem that you previously would have handled easily?",
    22: "How often do you lose track of the steps while performing an ordinary task?",
    23: "How often do you have difficulty finding familiar words while speaking or writing?",
    24: "How often do you become confused about dates, times, locations, or where you are going?",
    25: "Compared with your previous level of ability, how much decline have you noticed in your memory or thinking?",
    26: "How much have other people noticed a change in your memory, thinking, or ability to perform familiar tasks?",
    27: "How much has your memory or thinking difficulty progressively increased over time?",
    28: "How much difficulty do you have independently managing finances, payments, or important documents?",
    29: "How much difficulty do you have independently managing medications, appointments, schedules, or important responsibilities?",
    30: "How much difficulty do you have independently managing everyday activities such as cooking, shopping, transportation, or household tasks?",
}


def _domain_for(qid):
    for section in SECTIONS:
        lo, hi = section["range"]
        if lo <= qid <= hi:
            return section["id"]
    raise KeyError(qid)


def _scale_for(qid):
    if 1 <= qid <= 24:
        return "frequency"
    if 25 <= qid <= 27:
        return "change"
    return "independence"


QUESTIONS = [
    {"id": qid, "text": _QUESTION_TEXT[qid], "domain": _domain_for(qid), "scale": _scale_for(qid)}
    for qid in range(1, 31)
]
QUESTION_IDS = [q["id"] for q in QUESTIONS]

DOMAIN_QUESTIONS = {}
for _q in QUESTIONS:
    DOMAIN_QUESTIONS.setdefault(_q["domain"], []).append(_q["id"])

DOMAIN_IDS = [s["id"] for s in SECTIONS]
REQUIRED_FEATURE_KEYS = tuple(DOMAIN_IDS)

DISCLAIMER = (
    "This is a research screening result, not a clinical diagnosis. "
    "It has not been validated against DSM-5 criteria or any diagnostic "
    "reference standard. If you have concerns about your attention, memory, "
    "or thinking, please discuss them with a qualified healthcare professional."
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationError(ValueError):
    pass


def validate_answers(answers):
    if not isinstance(answers, dict):
        raise ValidationError("answers must be an object mapping question id to a numeric response")

    normalized = {}
    for qid in QUESTION_IDS:
        raw, found = None, False
        for k in (str(qid), qid):
            if k in answers:
                raw, found = answers[k], True
                break
        if not found:
            raise ValidationError(f"missing response for question {qid}")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValidationError(f"response for question {qid} must be an integer 0-4")
        if value < 0 or value > 4:
            raise ValidationError(f"response for question {qid} must be between 0 and 4")
        normalized[qid] = value

    extra = set()
    for k in answers.keys():
        try:
            ik = int(k)
        except (TypeError, ValueError):
            extra.add(k)
            continue
        if ik not in QUESTION_IDS:
            extra.add(k)
    if extra:
        raise ValidationError(f"unrecognized question id(s) in answers: {sorted(str(x) for x in extra)}")

    return normalized


def compute_domain_scores(answers):
    domains = {}
    for domain, qids in DOMAIN_QUESTIONS.items():
        vals = [answers[q] for q in qids]
        domains[domain] = round(sum(vals) / len(vals), 2)
    return domains


def validate_domain_features(features):
    if not isinstance(features, dict):
        raise ValidationError("features must be an object")
    missing = [k for k in REQUIRED_FEATURE_KEYS if k not in features]
    if missing:
        raise ValidationError(f"missing feature keys: {missing}")
    extra = [k for k in features if k not in REQUIRED_FEATURE_KEYS]
    if extra:
        raise ValidationError(f"unexpected feature keys: {extra}")
    normalized = {}
    for k in REQUIRED_FEATURE_KEYS:
        try:
            v = float(features[k])
        except (TypeError, ValueError):
            raise ValidationError(f"feature '{k}' must be numeric")
        if v < 0 or v > 4:
            raise ValidationError(f"feature '{k}' must be between 0 and 4")
        normalized[k] = round(v, 2)
    return normalized


# ---------------------------------------------------------------------------
# Classifier (see project README for the design rationale)
# ---------------------------------------------------------------------------

MIN_EVIDENCE_THRESHOLD = 1.3
AMBIGUITY_MARGIN = 0.35
ADHD_HISTORY_FLOOR = 1.0
DEMENTIA_FUNCTIONAL_FLOOR = 2.0

_HEALTHY_INPUT_DOMAINS = (
    "attention", "hyperactivity", "impulsivity", "memory", "executive",
    "language", "orientation", "cognitive_decline", "functional_impairment",
)


def _evidence_scores(d):
    adhd = (
        0.35 * d["attention"] + 0.20 * d["hyperactivity"]
        + 0.20 * d["impulsivity"] + 0.25 * d["childhood_history"]
    )

    mci_cognitive_signal = 0.35 * d["memory"] + 0.25 * d["executive"] + 0.30 * d["cognitive_decline"]
    functional_preserved_frac = max(0.0, 4 - d["functional_impairment"]) / 4
    mci = mci_cognitive_signal * (0.7 + 0.3 * functional_preserved_frac)

    dementia = (
        0.25 * d["memory"] + 0.15 * d["executive"] + 0.15 * d["orientation"]
        + 0.15 * d["language"] + 0.30 * d["functional_impairment"]
    )

    values = [d[k] for k in _HEALTHY_INPUT_DOMAINS]
    mean_impairment = sum(values) / len(values)
    max_impairment = max(values)
    impairment_measure = 0.35 * mean_impairment + 0.65 * max_impairment
    healthy = max(0.0, 4 - impairment_measure)

    return {
        "Probable ADHD": round(adhd, 2),
        "Probable MCI": round(mci, 2),
        "Probable Dementia": round(dementia, 2),
        "Likely Healthy": round(healthy, 2),
    }


def _top_contributing_domains(d, label, n=3):
    weights_by_label = {
        "Probable ADHD": {"attention": 0.35, "hyperactivity": 0.20, "impulsivity": 0.20, "childhood_history": 0.25},
        "Probable MCI": {"memory": 0.35, "executive": 0.25, "cognitive_decline": 0.30},
        "Probable Dementia": {"memory": 0.25, "executive": 0.15, "orientation": 0.15, "language": 0.15, "functional_impairment": 0.30},
        "Likely Healthy": {},
    }
    weights = weights_by_label.get(label, {})
    contributions = sorted(((k, d[k] * w) for k, w in weights.items()), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in contributions[:n] if d[k] > 0.5]


_RATIONALE_TEMPLATES = {
    "Probable ADHD": (
        "The response pattern shows elevated attention-related difficulties "
        "and a developmental history of similar difficulties, without a "
        "strong progressive cognitive-decline pattern."
    ),
    "Probable MCI": (
        "The response pattern contains memory and cognitive-decline signals "
        "while reported functional independence remains relatively preserved."
    ),
    "Probable Dementia": (
        "The response pattern shows cognitive difficulties across multiple "
        "domains together with meaningful impairment in independent daily "
        "functioning."
    ),
    "Likely Healthy": (
        "The response pattern shows low impairment across the measured "
        "attention and cognitive domains, with preserved daily functioning "
        "and no strong persistent symptom pattern."
    ),
    "Other / Indeterminate": (
        "The response pattern does not clearly match a single screening "
        "category -- evidence is mixed, overlapping across categories, or "
        "too limited to support a specific classification."
    ),
}


def classify(domains):
    for k in REQUIRED_FEATURE_KEYS:
        if k not in domains:
            raise ValidationError(f"missing domain feature: {k}")

    scores = _evidence_scores(domains)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_score = ranked[0]
    second_label, second_score = ranked[1]

    if top_score < MIN_EVIDENCE_THRESHOLD:
        label = "Other / Indeterminate"
    elif (top_score - second_score) < AMBIGUITY_MARGIN:
        label = "Other / Indeterminate"
    elif top_label == "Probable ADHD" and domains["childhood_history"] < ADHD_HISTORY_FLOOR:
        label = "Other / Indeterminate"
    elif top_label == "Probable MCI" and domains["functional_impairment"] >= DEMENTIA_FUNCTIONAL_FLOOR:
        if scores["Probable Dementia"] >= scores["Probable MCI"] - AMBIGUITY_MARGIN:
            label = "Probable Dementia"
        else:
            label = top_label
    else:
        label = top_label

    if label == "Other / Indeterminate":
        confidence = round(max(0.15, 0.5 - (MIN_EVIDENCE_THRESHOLD - top_score if top_score < MIN_EVIDENCE_THRESHOLD else (top_score - second_score))), 2)
        confidence = min(confidence, 0.45)
        rationale = _RATIONALE_TEMPLATES["Other / Indeterminate"]
        contributing = []
    else:
        spread = max(0.01, top_score - second_score)
        confidence = round(min(0.95, 0.45 + spread * 0.35 + (top_score / 4) * 0.2), 2)
        rationale = _RATIONALE_TEMPLATES[label]
        contributing = _top_contributing_domains(domains, label)

    return {
        "label": label,
        "confidence": confidence,
        "scores": scores,
        "domains": domains,
        "contributing_domains": contributing,
        "rationale": rationale,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Optional LLM feature extraction
# ---------------------------------------------------------------------------

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 12

SYSTEM_PROMPT = (
    "You are a research data extraction component. Convert the "
    "participant's questionnaire responses into structured cognitive and "
    "behavioral features. Do not diagnose the participant. Return only "
    "strict JSON matching the schema you are given, with no additional "
    "fields, commentary, or markdown formatting."
)


class LLMExtractionError(Exception):
    pass


def _build_user_prompt(answers):
    lines = [
        "Participant questionnaire responses (question id: response value, "
        "0-4 scale, higher = more frequent/severe):",
        "",
    ]
    by_id = {q["id"]: q for q in QUESTIONS}
    for qid in sorted(answers.keys()):
        q = by_id[qid]
        lines.append(f'Q{qid} [{q["domain"]}]: "{q["text"]}" -> {answers[qid]}')
    lines.append("")
    lines.append(
        "Aggregate these into the following domain features, each a number "
        "from 0 to 4 (can include one decimal place):"
    )
    lines.append(json.dumps({k: "0-4" for k in REQUIRED_FEATURE_KEYS}, indent=2))
    lines.append("\nReturn ONLY a JSON object with exactly these keys and numeric values in [0, 4].")
    return "\n".join(lines)


def llm_extract_domains(answers, api_key, model):
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(answers)},
        ],
    }
    req = urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMExtractionError(f"OpenAI API HTTP error: {e.code}") from e
    except urllib.error.URLError as e:
        raise LLMExtractionError(f"OpenAI API unreachable or timed out: {e}") from e
    except (TimeoutError, OSError) as e:
        raise LLMExtractionError(f"OpenAI API timeout: {e}") from e

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMExtractionError("unexpected OpenAI API response shape") from e
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        raise LLMExtractionError("LLM did not return valid JSON") from e
    try:
        return validate_domain_features(parsed)
    except ValidationError as e:
        raise LLMExtractionError(f"LLM JSON failed schema validation: {e}") from e


# ---------------------------------------------------------------------------
# Routes -- API
# ---------------------------------------------------------------------------

@app.get("/api/questions")
def get_questions():
    resp = jsonify({"questions": QUESTIONS, "sections": SECTIONS, "scales": SCALES})
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.post("/api/screen")
def post_screen():
    try:
        payload = request.get_json(force=False, silent=True)
    except Exception:
        payload = None

    if payload is None:
        return jsonify({"error": "request body must be valid JSON"}), 400

    if not isinstance(payload, dict) or "answers" not in payload:
        return jsonify({"error": 'request body must include an "answers" object'}), 400

    try:
        answers = validate_answers(payload["answers"])
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    use_llm = bool(payload.get("use_llm", False))
    extraction_method = "deterministic"
    domains = None

    if use_llm:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            extraction_method = "deterministic_fallback_no_api_key"
        else:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            try:
                domains = llm_extract_domains(answers, api_key=api_key, model=model)
                extraction_method = "llm"
            except LLMExtractionError:
                domains = None
                extraction_method = "deterministic_fallback_llm_error"

    if domains is None:
        domains = compute_domain_scores(answers)

    try:
        result = classify(domains)
    except ValidationError:
        return jsonify({"error": "internal scoring error"}), 500

    result["meta"] = {"extraction_method": extraction_method}
    return jsonify(result), 200


@app.errorhandler(500)
def handle_500(_e):
    return jsonify({"error": "an unexpected server error occurred"}), 500


# ---------------------------------------------------------------------------
# Routes -- static frontend
# ---------------------------------------------------------------------------

@app.get("/")
def serve_index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.get("/<path:filename>")
def serve_static(filename):
    return send_from_directory(PUBLIC_DIR, filename)
