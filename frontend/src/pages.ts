import { matchPath } from 'react-router'

export type Marketplace = 'ozon' | 'wb' | 'ym'

export interface Page {
  path: string
  label: string
  marketplace?: Marketplace
}

export const homePath = '/stock'
export const loginPath = '/login'
export const accessDeniedPage = { path: '/access-denied', label: 'Нет доступных магазинов' }
export const operationsPage = { path: '/stock/:slug/operations', label: 'История операций' }
export const arrivalsPage = { path: '/supply-schedule', label: 'Расписание поставок' }
export const profilePage = { path: '/profile', label: 'Профиль' }

export const stockPages: readonly Page[] = [
  { path: homePath, label: 'Остатки' },
  { path: '/stock/total', label: 'Остатки Тотал' },
  { path: '/stock/supplies', label: 'Поставки' },
  { path: '/stock/inbound', label: 'Поставки FBO' },
  { path: '/stock/cost-report', label: 'Движение и ЗЦ' },
  operationsPage,
]

const economics = '/sales/unit-economics-1c'

export const economicsPages: readonly Page[] = [
  { path: economics, label: 'Wildberries', marketplace: 'wb' },
  { path: economics + '/ozon', label: 'Ozon', marketplace: 'ozon' },
  { path: economics + '/yandex-market', label: 'Яндекс Маркет', marketplace: 'ym' },
]

export const reportPages: readonly Page[] = [
  { path: economics + '/reports/unit-profit', label: 'WB · Юниточная прибыль', marketplace: 'wb' },
  { path: economics + '/reports/target-price', label: 'WB · Целевая цена', marketplace: 'wb' },
  { path: economics + '/reports/standard-costs', label: 'WB · Расходы без ИУ', marketplace: 'wb' },
  { path: economics + '/reports/spp', label: 'WB · Отчёт по СПП', marketplace: 'wb' },
  { path: economics + '/reports/rating', label: 'WB · Рейтинг товаров', marketplace: 'wb' },
  { path: economics + '/yandex-market/reports/unit-profit', label: 'ЯМ · Юниточная прибыль', marketplace: 'ym' },
  { path: economics + '/yandex-market/reports/target-price', label: 'ЯМ · Целевая цена', marketplace: 'ym' },
  { path: economics + '/yandex-market/reports/spp', label: 'ЯМ · Отчёт по СПП', marketplace: 'ym' },
  { path: economics + '/ozon/reports/unit-profit', label: 'Ozon · Юниточная прибыль', marketplace: 'ozon' },
  { path: economics + '/ozon/reports/target-price', label: 'Ozon · Целевая цена', marketplace: 'ozon' },
]

export const agentPages: readonly Page[] = [
  { path: '/ai-agents/keys', label: 'API-ключи' },
  { path: '/ai-agents/catalog', label: 'Каталог API' },
  { path: '/ai-agents/instructions', label: 'Инструкция ИИ' },
  { path: '/ai-agents/gpt', label: 'ChatGPT' },
  { path: '/ai-agents/claude', label: 'Claude / MCP' },
  { path: '/ai-agents/gpt-mcp', label: 'ChatGPT / MCP' },
]

export const adminPage = { path: '/admin', label: 'Админ' }
export const integrationsPage = { path: '/admin/integrations', label: 'Интеграции' }
export const yandexFinanceUrl = 'https://153.76.164.196'

const analyticsMarketplaces = [
  { marketplace: 'ozon', label: 'Ozon', path: '/analytics/ozon' },
  { marketplace: 'wb', label: 'WB', path: '/analytics' },
  { marketplace: 'ym', label: 'ЯМ', path: '/analytics/yandex-market' },
] satisfies Page[]

export const analyticsGroups = analyticsMarketplaces.map(({ marketplace, label, path }) => ({
  marketplace,
  label,
  pages: [
    { path: path + '/analyzer', label: 'Анализатор', marketplace },
    { path: path + '/ephemerides', label: 'Эфемериды', marketplace },
    { path: path + '/sales-api', label: 'Продажи по API', marketplace },
  ],
}))

export const pages: readonly Page[] = [
  ...stockPages,
  arrivalsPage,
  ...analyticsGroups.flatMap((group) => group.pages),
  ...economicsPages,
  ...reportPages,
  ...agentPages,
  adminPage,
  integrationsPage,
  profilePage,
  accessDeniedPage,
]

export function isPageActive(page: Page, pathname: string) {
  return Boolean(matchPath(page.path, pathname))
}

export function returnPath(state: unknown) {
  if (typeof state !== 'object' || state === null || !('returnTo' in state)) return homePath
  if (typeof state.returnTo !== 'string' || !state.returnTo.startsWith('/')) return homePath
  try {
    const url = new URL(state.returnTo, window.location.origin)
    if (url.origin !== window.location.origin) return homePath
    if (!pages.some((page) => isPageActive(page, url.pathname))) return homePath
    return url.pathname + url.search + url.hash
  } catch {
    return homePath
  }
}
