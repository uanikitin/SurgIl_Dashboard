from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .settings import settings

# Engine с "подстраховкой" соединения (pool_pre_ping), async нам пока не нужен
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)

# Фабрика сессий: без автокоммита и автофлаша — контролируем транзакции явно
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# База для декларативных моделей ORM (классический SQLAlchemy)
Base = declarative_base()

def get_db():
    """Зависимость FastAPI: выдаёт Session и корректно закрывает её после запроса."""
    from sqlalchemy.orm import Session
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()