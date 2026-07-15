"""AI-powered vocabulary generation via the Anthropic API"""

import json
import os
from typing import Any, Dict, List

from anthropic import Anthropic

MODEL = "claude-haiku-4-5"

SCHEMA = """{
  "simplified": "simplified Chinese characters" *REQUIRED*,
  "traditional": "traditional Chinese characters (if different, else same as simplified)" *REQUIRED*,
  "pinyin": "pinyin with tone marks (e.g., nǐ hǎo)" *REQUIRED*,
  "english": "English translation" *REQUIRED*,
  "hsk_level": 1-6 or null if not HSK,
  "word_type": "noun/verb/adjective/etc" *REQUIRED*,
  "emoji": "relevant emoji" *REQUIRED*,
  "mnemonic": "creative, memorable memory aid" *REQUIRED*,
  "etymology": "character origins and evolution" *REQUIRED*,
  "measure_word": {"character": "...", "pinyin": "...", "example": "..."} or null (countable nouns only),
  "related_words": ["3+ related everyday words in simplified characters"] *REQUIRED*,
  "examples": [{"chinese": "...", "pinyin": "...", "english": "..."}] *REQUIRED - 2+ colloquial sentences*,
  "usage_notes": "brief grammar or usage notes"
}"""

STYLE_RULES = """IMPORTANT RULES:
- **CASUAL LANGUAGE FIRST**: always prefer the everyday spoken word over formal/written equivalents
  ("doctor" → 医生 NOT 执业医师; "teacher" → 老师 NOT 教育工作者; "eat" → 吃/吃饭 NOT 用餐)
- Use proper pinyin tone marks (ā á ǎ à, ē é ě è, ...)
- Examples must sound like real conversations, not textbook Chinese
- Etymology explains character origins; mnemonics help visualize the word"""


class WordGenerator:
    """Generate vocabulary entries using Claude"""

    def __init__(self):
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError(
                "AI word generation needs an Anthropic API key (only ct add / add-many / generate use one; "
                "reviewing works without it).\n"
                "   Get a key at https://console.anthropic.com, then: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        self.client = Anthropic(api_key=api_key)

    def generate_word(self, query: str) -> Dict[str, Any]:
        """One vocabulary entry from an English/Chinese query or description"""
        prompt = f"""You are a Chinese language expert specializing in everyday, conversational Chinese. Given a query (English, Chinese, or a description), generate one vocabulary entry.

Query: "{query}"

Generate a JSON object with these fields:

{SCHEMA}

{STYLE_RULES}

If the query doesn't match a valid Chinese word, use the closest casual/colloquial match.
Return ONLY the JSON object, no additional text."""
        result = self._request(prompt, max_tokens=16000)
        return self._validate(result)

    def generate_many_words(self, description: str, count: int) -> List[Dict[str, Any]]:
        """A batch of thematically related vocabulary entries"""
        prompt = f"""You are a Chinese language expert specializing in everyday, conversational Chinese. Generate {count} vocabulary words for this description, varying HSK levels appropriately for the topic.

Description: "{description}"

Generate a JSON array of {count} objects, each with these fields:

{SCHEMA}

{STYLE_RULES}

Return ONLY the JSON array, no additional text."""
        result = self._request(prompt, max_tokens=64000)
        if not isinstance(result, list):
            raise ValueError("Expected a JSON array of vocabulary objects")
        return [self._validate(word) for word in result]

    def _request(self, prompt: str, max_tokens: int) -> Any:
        # Streaming avoids the SDK's long-request timeout on big batches
        content = ""
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                content += text

        content = content.strip()
        if content.startswith('```'):
            content = '\n'.join(
                line for line in content.splitlines() if not line.strip().startswith('```')
            )
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude returned invalid JSON: {e}\nResponse: {content[:500]}")

    @staticmethod
    def _validate(word: Dict[str, Any]) -> Dict[str, Any]:
        for field in ('simplified', 'pinyin', 'english'):
            if not word.get(field):
                raise ValueError(f"Generated word missing required field: {field}")
        return word


def save_word(db, word: Dict[str, Any], source: str) -> bool:
    """Serialize and insert a generated word; returns False if it already existed"""
    from importer import serialize_json_fields
    word = serialize_json_fields(dict(word))
    word['source'] = source
    return db.add_vocabulary(word) is not None


def preview(word: Dict[str, Any], index: int = None) -> str:
    """One-line summary of a generated word for confirmation prompts"""
    prefix = f"{index:3}. " if index is not None else ""
    hanzi = word['simplified']
    if word.get('traditional') and word['traditional'] != hanzi:
        hanzi += f" ({word['traditional']})"
    emoji = f" {word['emoji']}" if word.get('emoji') else ""
    return f"{prefix}{hanzi}{emoji}  {word['pinyin']} - {word['english']}"
