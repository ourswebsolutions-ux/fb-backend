try:
    import google.genai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

from app.core.config import settings


class AIService:
    def __init__(self):
        self.model = "gemini-2.0-flash"
        if GENAI_AVAILABLE and settings.emergent_llm_key:
            self.client = genai.Client(api_key=settings.emergent_llm_key)
        else:
            self.client = None

    def _chat(self, session_id: str, system_message: str = ""):
        if not GENAI_AVAILABLE:
            raise ImportError("google-genai package is not available")
        return self.client

    async def generate_listing(
        self,
        product_name: str,
        category: str,
        condition: str,
        price: int,
        extra_details: str = "",
        session_id: str = "listing_gen",
    ) -> dict[str, str]:
        if not self.client:
            raise ImportError("AI client not available - check API key configuration")
        
        system_message = (
            "You are an expert Facebook Marketplace listing copywriter. "
            "Write concise, engaging, and SEO-friendly product titles and descriptions. "
            "Never include emojis unless asked. Keep descriptions factual and compelling."
        )
        prompt = (
            f"Generate a Facebook Marketplace listing for:\n"
            f"Product: {product_name}\n"
            f"Category: {category}\n"
            f"Condition: {condition}\n"
            f"Price: ${price / 100:.2f}\n"
            f"Extra details: {extra_details}\n\n"
            f"Return ONLY valid JSON with keys 'title' (max 100 chars) and 'description' (max 500 chars). "
            f"Do not include any markdown or code fences."
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=genai.GenerateContentConfig(
                system_instruction=system_message
            )
        )
        
        import json, re
        text = response.text.strip()
        # Strip markdown fences if present
        text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
        data = json.loads(text)
        return {"title": data.get("title", ""), "description": data.get("description", "")}

    async def generate_reply(
        self,
        original_message: str,
        sender_name: str,
        tone: str = "friendly",
        custom_instructions: str = "",
        session_id: str = "reply_gen",
    ) -> str:
        if not self.client:
            raise ImportError("AI client not available - check API key configuration")
        
        system_message = (
            "You are a Facebook Marketplace seller responding to buyer messages. "
            "Be helpful, direct, and professional."
        )
        prompt = (
            f"Buyer name: {sender_name}\n"
            f"Buyer message: {original_message}\n"
            f"Tone: {tone}\n"
            f"Additional instructions: {custom_instructions}\n\n"
            f"Write a short, natural reply. Keep it under 200 characters. "
            f"Be helpful and direct. Do not include emojis or markdown."
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=genai.GenerateContentConfig(
                system_instruction=system_message
            )
        )
        return response.text.strip()

    async def improve_description(
        self,
        title: str,
        description: str,
        session_id: str = "improve_desc",
    ) -> str:
        if not self.client:
            raise ImportError("AI client not available - check API key configuration")
        
        system_message = (
            "You are an expert Facebook Marketplace listing copywriter. "
            "Improve descriptions to be more engaging and SEO-friendly."
        )
        prompt = (
            f"Improve this Facebook Marketplace listing description.\n"
            f"Title: {title}\n"
            f"Current description: {description}\n\n"
            f"Return ONLY the improved description text, no JSON, no markdown."
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=genai.GenerateContentConfig(
                system_instruction=system_message
            )
        )
        return response.text.strip()
