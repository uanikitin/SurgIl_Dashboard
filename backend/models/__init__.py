# пустой файл или импорт моделей
# backend/models/__init__.py
from .wells import Well
from .events import Event
from .users import User
from .well_status import WellStatus
from .well_equipment import WellEquipment  # ← вот это важно
from .well_notes import WellNote
from .users import DashboardUser, DashboardLoginLog



