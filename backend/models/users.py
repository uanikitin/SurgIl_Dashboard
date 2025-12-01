# backend/models/user.py
from sqlalchemy import Column, BigInteger, Text
from ..db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(Text)
    full_name = Column(Text)

