# Добавьте в backend/services/well_service.py (создайте файл):
from sqlalchemy.orm import Session
from backend.models.wells import Well


def get_well_by_identifier(db: Session, identifier: str | int):
    """
    Универсальная функция получения скважины.
    Принимает и ID, и номер скважины.
    Возвращает объект Well или None.
    """
    # Сначала пробуем как ID
    if isinstance(identifier, (int, str)) and str(identifier).isdigit():
        well = db.query(Well).filter(Well.id == int(identifier)).first()
        if well:
            return well

    # Если не нашли по ID или identifier не число, пробуем как номер
    well = db.query(Well).filter(Well.number == str(identifier)).first()
    return well


# И в app.py импортируйте и используйте:
from backend.services.well_service import get_well_by_identifier


@app.get("/well/{well_identifier}")
def well_page(
        well_identifier: str,  # Может быть и ID, и номер
        # ...
):
    well = get_well_by_identifier(db, well_identifier)
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # Все остальные запросы используют well.id
    # ...