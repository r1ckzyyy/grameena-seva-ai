"""Persistent farmer identity and memory service."""

from __future__ import annotations

from uuid import uuid4

from models.conversation import ConversationState
from repositories.conversations import ConversationRepository
from repositories.farmers import FarmerRepository, normalize_phone
from repositories.models import FarmerRecord


class FarmerProfileService:
    """Create, load, update, and delete durable farmer profiles."""

    def __init__(
        self,
        farmers: FarmerRepository,
        conversations: ConversationRepository,
    ) -> None:
        self.farmers = farmers
        self.conversations = conversations

    def start_new_farmer(self) -> FarmerRecord:
        """Create a fresh temporary profile without modifying previous farmers."""
        farmer = FarmerRecord(id=str(uuid4()))
        self.farmers.save(farmer)
        return farmer

    def get(self, farmer_id: str) -> FarmerRecord | None:
        return self.farmers.get(farmer_id)

    def find_by_phone(self, phone: str) -> FarmerRecord | None:
        """Return an existing farmer profile matching a phone number."""
        normalized = normalize_phone(phone)
        if not normalized:
            return None
        return self.farmers.find_by_phone(normalized)

    def load_or_create(self, farmer_id: str | None = None, phone: str = "") -> tuple[FarmerRecord, ConversationState | None]:
        """Load an existing farmer and conversation or create a new profile."""
        if phone:
            normalized = normalize_phone(phone)
            if not normalized:
                raise ValueError("Enter a valid 10-digit mobile number.")
            existing = self.find_by_phone(normalized)
            if existing:
                conversation = self.conversations.load_for_farmer(existing.id)
                return existing, conversation
            farmer = FarmerRecord(id=normalized, phone=normalized)
            self.farmers.save(farmer)
            return farmer, None
        if farmer_id:
            farmer = self.farmers.get(farmer_id)
            if farmer:
                return farmer, self.conversations.load_for_farmer(farmer_id)
        farmer = self.start_new_farmer()
        return farmer, None

    def memory_context(self, farmer: FarmerRecord) -> str:
        """Build a compact memory block for the conversational agent."""
        parts: list[str] = []
        if farmer.name:
            parts.append(f"Name: {farmer.name}")
        if farmer.phone:
            parts.append(f"Phone: {farmer.phone}")
        if farmer.village:
            parts.append(f"Village: {farmer.village}")
        if farmer.district:
            parts.append(f"District: {farmer.district}")
        if farmer.state:
            parts.append(f"State: {farmer.state}")
        if farmer.land_size:
            parts.append(f"Land size: {farmer.land_size}")
        if farmer.farmer_category:
            parts.append(f"Category: {farmer.farmer_category}")
        if farmer.current_crops:
            parts.append(f"Current crops: {', '.join(farmer.current_crops)}")
        if farmer.previous_crops:
            parts.append(f"Previous crops: {', '.join(farmer.previous_crops)}")
        if farmer.soil_type:
            parts.append(f"Soil: {farmer.soil_type}")
        if farmer.irrigation_source:
            parts.append(f"Irrigation: {farmer.irrigation_source}")
        if farmer.owned_equipment:
            parts.append(f"Equipment: {', '.join(farmer.owned_equipment)}")
        if farmer.recommendations:
            parts.append(f"Previous recommendations: {', '.join(farmer.recommendations[-5:])}")
        if farmer.eligible_schemes:
            parts.append(f"Eligible schemes: {', '.join(farmer.eligible_schemes[-5:])}")
        if farmer.conversation_summaries:
            parts.append(f"Recent summaries: {' | '.join(farmer.conversation_summaries[-3:])}")
        if not parts:
            return ""
        return "Known farmer memory (do not ask again for these details):\n" + "\n".join(parts)

    def has_memory(self, farmer: FarmerRecord) -> bool:
        """Return True when the farmer profile contains reusable facts."""
        return any([
            farmer.name, farmer.phone, farmer.village, farmer.district, farmer.state,
            farmer.land_size, farmer.farmer_category, farmer.current_crops, farmer.owned_equipment,
            farmer.recommendations, farmer.eligible_schemes, farmer.conversation_summaries,
        ])

    def sync_from_conversation(self, farmer_id: str, conversation: ConversationState, *, eligibility_status: str = "") -> FarmerRecord:
        """Merge facts learned in a conversation into the farmer memory."""
        farmer = self.farmers.get(farmer_id) or FarmerRecord(id=farmer_id)
        result = conversation.result
        if result.name:
            farmer.name = result.name
        if result.mobile_number:
            normalized_phone = normalize_phone(result.mobile_number)
            if normalized_phone:
                farmer.phone = normalized_phone
        if result.state:
            farmer.state = result.state
        if result.district:
            farmer.district = result.district
        if result.village:
            farmer.village = result.village
        if result.land_size:
            farmer.land_size = result.land_size
        if result.farmer_category:
            farmer.farmer_category = result.farmer_category
        if result.major_crop and result.major_crop not in farmer.current_crops:
            farmer.current_crops.append(result.major_crop)
        if result.equipment_or_input and result.equipment_or_input not in farmer.owned_equipment:
            farmer.owned_equipment.append(result.equipment_or_input)
        if result.scheme_name and result.scheme_name not in farmer.recommendations:
            farmer.recommendations.append(result.scheme_name)
        if eligibility_status == "ELIGIBLE" and result.scheme_name and result.scheme_name not in farmer.eligible_schemes:
            farmer.eligible_schemes.append(result.scheme_name)
        self.farmers.save(farmer)
        return farmer

    def update_fields(self, farmer_id: str, fields: dict[str, str]) -> FarmerRecord:
        """Merge explicitly supplied profile fields into persistent memory."""
        farmer = self.farmers.get(farmer_id) or FarmerRecord(id=farmer_id)
        mapping = {
            "farmer_name": "name",
            "mobile_number": "phone",
            "district": "district",
            "village": "village",
            "state": "state",
            "land_size": "land_size",
            "farmer_category": "farmer_category",
            "equipment_or_input": "owned_equipment",
        }
        for source, target in mapping.items():
            value = str(fields.get(source) or "").strip()
            if not value:
                continue
            if target == "owned_equipment":
                if value not in farmer.owned_equipment:
                    farmer.owned_equipment.append(value)
            elif target == "phone":
                normalized_phone = normalize_phone(value)
                if normalized_phone:
                    farmer.phone = normalized_phone
            else:
                setattr(farmer, target, value)
        self.farmers.save(farmer)
        return farmer

    def delete_farmer(self, farmer_id: str) -> None:
        """Delete a farmer and all directly associated records."""
        self.conversations.delete_for_farmer(farmer_id)
        self.farmers.delete(farmer_id)

    def append_summary(self, farmer_id: str, summary: str) -> None:
        """Persist one concise completed-conversation summary."""
        summary = (summary or "").strip()
        if not summary:
            return
        farmer = self.farmers.get(farmer_id)
        if farmer is None:
            return
        if summary not in farmer.conversation_summaries:
            farmer.conversation_summaries.append(summary)
            farmer.conversation_summaries = farmer.conversation_summaries[-20:]
            self.farmers.save(farmer)
