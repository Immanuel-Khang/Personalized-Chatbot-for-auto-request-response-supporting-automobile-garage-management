"""DataHub: Deterministic data retrieval layer for Coordinator context preparation.

NO LLM calls here. Pure Python logic wrapping existing services.
Returns structured results with explicit states: FOUND, NOT_FOUND, AMBIGUOUS, ERROR.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.contracts.schemas import Product
from app.services import get_services


class DataState(str, Enum):
    """Result state of a DataHub fetch operation."""
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"  # Multiple matches, need clarification
    ERROR = "ERROR"


@dataclass
class DataHubResult:
    """Standard result container for all DataHub operations."""
    state: DataState
    data: Any = None
    message: str = ""
    alternatives: list[Any] | None = None  # For AMBIGUOUS state


class DataHub:
    """Deterministic data retrieval for coordinator context preparation."""

    def __init__(self):
        self.services = get_services()

    def fetch_product_by_name(self, name: str, exact: bool = False) -> DataHubResult:
        """Find product by name. Returns AMBIGUOUS if multiple matches."""
        if not name or not name.strip():
            return DataHubResult(DataState.NOT_FOUND, message="Empty product name")

        try:
            products = self.services.product.find_model(name, limit=5)
            if not products:
                return DataHubResult(DataState.NOT_FOUND, message=f"No product found: {name}")

            if len(products) == 1:
                return DataHubResult(DataState.FOUND, data=products[0])

            # Multiple matches: check for exact name match
            if exact:
                exact_matches = [p for p in products if p.name.lower() == name.lower()]
                if len(exact_matches) == 1:
                    return DataHubResult(DataState.FOUND, data=exact_matches[0])

            return DataHubResult(
                DataState.AMBIGUOUS,
                data=products[0],  # Return best match as primary
                alternatives=products[1:],
                message=f"Multiple products match '{name}'"
            )
        except Exception as e:
            return DataHubResult(DataState.ERROR, message=f"Error fetching product: {e}")

    def fetch_msrp(self, car_model: str) -> DataHubResult:
        """Fetch MSRP (list price) for a car model. Used for cross-domain contract needs."""
        result = self.fetch_product_by_name(car_model)
        if result.state != DataState.FOUND:
            return result

        product: Product = result.data
        if product.list_price_vnd is None:
            return DataHubResult(DataState.NOT_FOUND, message=f"No MSRP data for {car_model}")

        return DataHubResult(
            DataState.FOUND,
            data={
                "msrp_vnd": product.list_price_vnd,
                "price_as_of": product.price_as_of,
                "product_name": product.name,
            }
        )

    def check_slot_available(self, service_type: str, appointment_date: str,
                           start_time: str | None) -> DataHubResult:
        """Check if appointment slot is available. Returns FOUND if available."""
        try:
            from datetime import date, time
            from app.contracts.schemas import ServiceType

            # Parse inputs
            try:
                svc_type = ServiceType(service_type)
            except ValueError:
                return DataHubResult(DataState.ERROR, message=f"Invalid service_type: {service_type}")

            appt_date = date.fromisoformat(appointment_date)
            appt_time = time.fromisoformat(start_time) if start_time else None

            # Use a dummy session_id for read-only check
            check_result = self.services.booking.check_slot(
                session_id="_datahub_check",
                service_type=svc_type,
                appointment_date=appt_date,
                start_time=appt_time
            )

            if check_result.ok:
                return DataHubResult(DataState.FOUND, data={"available": True})
            else:
                return DataHubResult(
                    DataState.NOT_FOUND,
                    data={"available": False, "reason": check_result.status},
                    message=check_result.message or "Slot not available"
                )
        except Exception as e:
            return DataHubResult(DataState.ERROR, message=f"Error checking slot: {e}")

    def validate_phone(self, phone: str) -> DataHubResult:
        """Validate phone number format."""
        from app.identity import normalize_phone

        if not phone:
            return DataHubResult(DataState.NOT_FOUND, message="Empty phone number")

        normalized = normalize_phone(phone)
        if not normalized or len(normalized) < 10:
            return DataHubResult(DataState.ERROR, message=f"Invalid phone format: {phone}")

        return DataHubResult(DataState.FOUND, data={"normalized": normalized})

    def get_product_specs(self, car_model: str) -> DataHubResult:
        """Fetch full product specifications for a car model."""
        result = self.fetch_product_by_name(car_model)
        if result.state != DataState.FOUND:
            return result

        product: Product = result.data
        return DataHubResult(
            DataState.FOUND,
            data={
                "specs": product.specs,
                "sales_info": product.sales_info,
                "name": product.name,
                "price": product.list_price_vnd,
            }
        )
