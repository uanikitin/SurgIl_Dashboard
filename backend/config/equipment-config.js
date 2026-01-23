// Конфигурация оборудования
export const equipmentConfig = {
  // Типы оборудования
  types: {
    'wellhead_sensor': 'Датчик устья',
    'line_sensor': 'Датчик шлейфа',
    'annulus_sensor': 'Датчик затрубного',
    'wellhead_gateway': 'Шлюз устьевой'
  },

  // Иконки оборудования
  icons: {
    'wellhead_sensor': '/static/icons/SMOD.png',
    'line_sensor': '/static/icons/SMOD.png',
    'annulus_sensor': '/static/icons/SMOD.png',
    'wellhead_gateway': '/static/icons/WH_launcer.png',
    'default': '/static/icons/default.png'
  },

  // Можно добавить цвета и другую конфигурацию
  colors: {
    'wellhead_sensor': '#3b82f6',
    'line_sensor': '#16a34a',
    'annulus_sensor': '#f59e0b',
    'wellhead_gateway': '#ef4444'
  }
}

// Вспомогательные функции
export const getEquipmentType = (code) => {
  return equipmentConfig.types[code] || 'Неизвестный тип'
}

export const getEquipmentIcon = (code) => {
  return equipmentConfig.icons[code] || equipmentConfig.icons.default
}

export const getEquipmentColor = (code) => {
  return equipmentConfig.colors[code] || '#cccccc'
}