import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from nltk import word_tokenize, sent_tokenize
from utils import safe_number_format

# Ensure required NLTK resources are present. If not available, download.
try:
    nltk.data.find('tokenizers/punkt')
except:
    nltk.download('punkt')

try:
    nltk.data.find('sentiment/vader_lexicon')
except:
    nltk.download('vader_lexicon')

sia = SentimentIntensityAnalyzer()

def word_level_sentiment(text):
    words = word_tokenize(text)
    results = []
    for w in words:
        score = sia.polarity_scores(w)['compound']
        label = label_from_score(score)
        results.append({"word": w, "score": safe_number_format(score), "label": label})
    return results

def sentence_level_sentiment(text):
    sents = sent_tokenize(text)
    results = []
    for s in sents:
        score = sia.polarity_scores(s)['compound']
        label = label_from_score(score)
        results.append({"sentence": s, "score": safe_number_format(score), "label": label})
    return results

def overall_sentiment(text):
    score = sia.polarity_scores(text)['compound']
    label = label_from_score(score)
    return {"score": safe_number_format(score), "label": label}

def label_from_score(score):
    # thresholds can be tuned
    if score >= 0.05:
        return "positive"
    elif score <= -0.05:
        return "negative"
    else:
        return "neutral"

def ai_reasoning(text):
    # Basic explainability: show top positive/negative sentences and words
    sents = sentence_level_sentiment(text)
    words = word_level_sentiment(text)
    sorted_sents_pos = sorted(sents, key=lambda x: x['score'], reverse=True)[:3]
    sorted_sents_neg = sorted(sents, key=lambda x: x['score'])[:3]
    top_pos_words = sorted([w for w in words if w['label']=='positive'], key=lambda x: x['score'], reverse=True)[:5]
    top_neg_words = sorted([w for w in words if w['label']=='negative'], key=lambda x: x['score'])[:5]
    overall = overall_sentiment(text)
    explanation = {
        "summary": f"Overall label: {overall['label']} (score {overall['score']}).",
        "top_positive_sentences": sorted_sents_pos,
        "top_negative_sentences": sorted_sents_neg,
        "top_positive_words": top_pos_words,
        "top_negative_words": top_neg_words,
        "why": "VADER lexicon-based scoring: positive/negative words and valence shifters (not, very, but) influence compound score."
    }
    return explanation

def analyze_text_from_string(text: str):
    # sanitize minimal
    text = text.strip()
    if not text:
        return {"error":"empty text"}
    return {
        "word_level": word_level_sentiment(text),
        "sentence_level": sentence_level_sentiment(text),
        "overall": overall_sentiment(text),
        "ai_reasoning": ai_reasoning(text)
    }
