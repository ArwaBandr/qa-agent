"""
Brain — LLM client abstraction.
Supports Claude, OpenAI, and Ollama with vision capabilities.
Sends page context + screenshots and gets structured decisions back.
"""

import base64
import json
import os
import time
from typing import Optional

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


FAST_MODELS = {
    "claude": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


class LLMClient:
    """Unified interface to call any supported LLM provider."""

    def __init__(self, provider: str, model: str, api_key: str = "", base_url: str = "", fast_model: str = ""):
        self.provider = provider.lower()
        self.model = model
        self.fast_model = fast_model or FAST_MODELS.get(self.provider) or model
        self.api_key = api_key or os.environ.get("EXPLORER_LLM_API_KEY", "")
        self.base_url = base_url
        self._client = None
        self._init_client()

    def _init_client(self):
        if self.provider == "claude":
            import anthropic
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        elif self.provider in ("openai", "local"):
            import openai
            import httpx
            kwargs = {}
            if self.provider == "local":
                # Local OpenAI-compatible server (LM Studio, vLLM, LocalAI, etc.)
                kwargs["base_url"] = self.base_url or "http://localhost:1234/v1"
                kwargs["api_key"] = self.api_key or "not-needed"
                kwargs["http_client"] = httpx.Client(verify=False)
            else:
                kwargs["api_key"] = self.api_key
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                    kwargs["http_client"] = httpx.Client(verify=False)
            self._client = openai.OpenAI(**kwargs)
        elif self.provider == "ollama":
            # Ollama uses its own HTTP API
            pass
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}. Use: claude, openai, ollama, local")

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        screenshot_paths: list[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Send a message to the LLM and get a text response. Retries on transient errors."""
        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "claude":
                    return self._chat_claude(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
                elif self.provider in ("openai", "local"):
                    return self._chat_openai(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
                elif self.provider == "ollama":
                    return self._chat_ollama(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
            except Exception as e:
                err_str = str(e).lower()
                if attempt < MAX_RETRIES - 1 and ("overloaded" in err_str or "529" in err_str or "rate" in err_str or "500" in err_str):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise

    def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        screenshot_paths: list[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> dict:
        """Send a message and parse the response as JSON."""
        raw = self.chat(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
        return self._extract_json(raw)

    def chat_fast(
        self,
        system_prompt: str,
        user_message: str,
        screenshot_paths: list[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Use the fast/cheap model for quick evaluations."""
        original_model = self.model
        self.model = self.fast_model
        try:
            return self.chat(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
        finally:
            self.model = original_model

    def chat_json_fast(
        self,
        system_prompt: str,
        user_message: str,
        screenshot_paths: list[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict:
        """Use the fast/cheap model and parse response as JSON."""
        raw = self.chat_fast(system_prompt, user_message, screenshot_paths, temperature, max_tokens)
        return self._extract_json(raw)

    # ── Claude ─────────────────────────────────────────────────

    def _chat_claude(self, system_prompt, user_message, screenshot_paths, temperature, max_tokens) -> str:
        content = []

        if screenshot_paths:
            for path in screenshot_paths:
                img_data = self._load_image_base64(path)
                if img_data:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_data,
                        },
                    })

        content.append({"type": "text", "text": user_message})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        return response.content[0].text

    # ── OpenAI ─────────────────────────────────────────────────

    def _chat_openai(self, system_prompt, user_message, screenshot_paths, temperature, max_tokens) -> str:
        content = []

        if screenshot_paths:
            for path in screenshot_paths:
                img_data = self._load_image_base64(path)
                if img_data:
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_data}",
                            "detail": "high",
                        },
                    })

        content.append({"type": "text", "text": user_message})

        response = self._client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )

        return response.choices[0].message.content

    # ── Ollama ─────────────────────────────────────────────────

    def _chat_ollama(self, system_prompt, user_message, screenshot_paths, temperature, max_tokens) -> str:
        import urllib.request
        import urllib.error

        images = []
        if screenshot_paths:
            for path in screenshot_paths:
                img_data = self._load_image_base64(path)
                if img_data:
                    images.append(img_data)

        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message, "images": images} if images
                else {"role": "user", "content": user_message},
            ],
        }

        ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        req = urllib.request.Request(
            f"{ollama_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        return result.get("message", {}).get("content", "")

    # ── Helpers ─────────────────────────────────────────────────

    def _load_image_base64(self, path: str) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return None

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = text.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` block
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            try:
                return json.loads(text[start:end].strip())
            except json.JSONDecodeError:
                pass

        # Try extracting from ``` ... ``` block
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            try:
                return json.loads(text[start:end].strip())
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } or [ ... ]
        for open_char, close_char in [("{", "}"), ("[", "]")]:
            if open_char in text:
                start = text.index(open_char)
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == open_char:
                        depth += 1
                    elif text[i] == close_char:
                        depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break

        return {"raw_response": text, "parse_error": True}
