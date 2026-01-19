# пустой файл или импорт моделей
# backend/models/__init__.py
from .wells import Well
from .events import Event
from .users import User
from .well_status import WellStatus
from .well_equipment import WellEquipment  # ← вот это важно
from .well_notes import WellNote
from .users import DashboardUser, DashboardLoginLog
from .reagent_inventory import ReagentInventorySnapshot

from .reagent_catalog import ReagentCatalog
from .reagents import ReagentSupply
from .reagent_inventory import ReagentInventorySnapshot
# backend/models/__init__.py

# from backend.db import Base

# здесь уже, вероятно, импортируются остальные модели
# например:
# from .well import Well
# from .event import Event
# ...

from .reagents import ReagentSupply  # ВОТ ТУТ, а не в backend/__init__.py