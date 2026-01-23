#!/bin/bash
echo "Исправление функции well_page..."

# 1. Создаем backup
cp backend/app.py backend/app.py.backup.$(date +%Y%m%d_%H%M%S)

# 2. Исправляем все фильтры
echo "Исправляю фильтры оборудования..."
sed -i '' 's/EquipmentInstallation\.well_id == well_id/EquipmentInstallation.well_id == well.id/g' backend/app.py
sed -i '' 's/WellEquipment\.well_id == well_id/WellEquipment.well_id == well.id/g' backend/app.py
sed -i '' 's/WellChannel\.well_id == well_id/WellChannel.well_id == well.id/g' backend/app.py
sed -i '' 's/WellNote\.well_id == well_id/WellNote.well_id == well.id/g' backend/app.py
sed -i '' 's/WellStatus\.well_id == well_id/WellStatus.well_id == well.id/g' backend/app.py

# 3. Добавляем редирект (вручную, нужно найти правильное место)
echo ""
echo "Добавьте этот код в app.py для редиректа:"
echo "=========================================="
cat << 'REDIRECT_CODE'
@app.get("/well/number/{well_number}")
def redirect_well_by_number(
    well_number: int,
    db: Session = Depends(get_db)
):
    """Редирект: /well/number/51 → /well/15"""
    well = db.query(Well).filter(Well.number == well_number).first()
    if not well:
        raise HTTPException(status_code=404, detail=f"Скважина с номером {well_number} не найдена")
    
    return RedirectResponse(f"/well/{well.id}")
REDIRECT_CODE
echo "=========================================="

echo "Готово! Проверьте:"
echo "1. /well/15 - должна показывать скважину №51 с оборудованием"
echo "2. /well/number/51 - редирект на /well/15"
