"""
Константы для расчёта дебита газа через штуцер.

Вынесены из кода для прозрачности и возможности изменения
без правки логики расчёта.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FlowRateConfig:
    """Параметры формулы истечения газа через штуцер."""
    C1: float = 2.919           # 4.17 * 0.7 — коэфф. расхода
    C2: float = 4.654           # нормирующий диаметр штуцера, мм
    C3: float = 286.95          # удельная газовая постоянная, Дж/(кг·К)
    multiplier: float = 4.1     # калибровочный множитель (единый для всех скважин)
    critical_ratio: float = 0.5 # порог критического истечения


@dataclass(frozen=True)
class PurgeLossConfig:
    """Параметры расчёта потерь при продувках (стравливание в атмосферу)."""
    choke_diameter_m: float = 0.03       # диаметр штуцера продувки, м
    gas_constant: float = 8314.0         # Дж/(кмоль·К)
    molar_mass: float = 18.0             # кг/кмоль (TODO: уточнить по PVT)
    discharge_coeff: float = 0.9         # коэфф. расхода
    standard_temp_K: float = 273.15      # стандартная температура, К
    atm_pressure_MPa: float = 0.101325   # атмосферное давление, МПа
    kgscm2_to_MPa: float = 0.0980665    # пересчёт кгс/см² → МПа


@dataclass(frozen=True)
class DowntimeConfig:
    """Параметры детекции простоев."""
    min_blowout_minutes: int = 30  # минимальная длительность «продувки»


# Экземпляры по умолчанию
DEFAULT_FLOW = FlowRateConfig()
DEFAULT_PURGE = PurgeLossConfig()
DEFAULT_DOWNTIME = DowntimeConfig()
