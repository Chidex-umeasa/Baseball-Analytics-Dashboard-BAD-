from sqlalchemy import Column, String, Boolean, Text, DateTime
from datetime import datetime
from backend.database import Base


class GameSession(Base):
    __tablename__ = "game_sessions"

    game_id = Column(String, primary_key=True, index=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    game_date = Column(String, nullable=False)
    session_data = Column(Text, nullable=False)
    saved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
