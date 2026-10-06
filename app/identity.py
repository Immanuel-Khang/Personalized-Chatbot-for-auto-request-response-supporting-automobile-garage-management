"""[TRACK C] Identity: visitor_token -> customer. TODO[PLATFORM]: nhận diện theo SĐT, OTP, gộp khách (soft merge)."""
from sqlalchemy.orm import Session

from app.db.models import Customer


def resolve_customer(db: Session, visitor_token: str) -> Customer:
    customer = db.query(Customer).filter_by(visitor_token=visitor_token).first()
    if not customer:
        customer = Customer(visitor_token=visitor_token)
        db.add(customer)
        db.flush()
    return customer
