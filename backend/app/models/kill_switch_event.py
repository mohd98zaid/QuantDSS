from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.core.database import Base

class KillSwitchEvent(Base):
    __tablename__ = "kill_switch_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    triggered_at = Column(DateTime(timezone=True), server_default=func.now())
    triggered_by = Column(String(50), nullable=False)
    reason = Column(String(255), nullable=True)
    state = Column(String(50), nullable=False)

    def __repr__(self):
        return f"<KillSwitchEvent {self.state} by {self.triggered_by} at {self.triggered_at}>"
