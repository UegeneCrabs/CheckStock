import { Link, NavLink, useLocation } from 'react-router'
import type { SessionProfile } from '../auth/api'
import { adminPage, integrationsPage, isPageActive, pages, profilePage } from '../pages'
import type { Marketplace } from '../pages'
import { Icon } from './Icon'
import { Navigation } from './Navigation'

const marketplaceNames: Record<Marketplace, string> = {
  wb: 'Wildberries',
  ozon: 'Ozon',
  ym: 'Яндекс Маркет',
}

interface HeaderProps {
  profile: SessionProfile
  onLogout: () => Promise<void>
  pending: boolean
}

export function Header({ profile, onLogout, pending }: HeaderProps) {
  const { pathname } = useLocation()
  const marketplace = pages.find((page) => isPageActive(page, pathname))?.marketplace
  const marketplaceName = marketplace && marketplaceNames[marketplace]
  const initials = profile.full_name.trim().split(/\s+/).slice(0, 2)
    .map((word) => Array.from(word)[0]).join('').toUpperCase() || 'CS'

  return (
    <header id="app-header" className={`sidebar app-header${marketplace ? ` marketplace-${marketplace}` : ''}`}>
      <div className="sidebar-brand-row">
        <Link className="brand" to="/" aria-label="РАКЕТА — на главную">
          <span className="brand-mark">Р</span>
          <span className="brand-label">РАКЕТА</span>
        </Link>
        {marketplaceName && <span className="marketplace-badge ui-marketplace-badge">{marketplaceName}</span>}
      </div>
      <Navigation key={pathname} pathname={pathname} store={profile.first_store_slug} />
      <div className="sidebar-bottom">
        <div className="nav-utility">
          <NavLink className="nav-item" to={adminPage.path} title="Админ-панель" aria-label="Админ-панель" end>
            <Icon name="admin" /><span>{adminPage.label}</span>
          </NavLink>
          <NavLink className="nav-item" to={integrationsPage.path} title="Интеграции и выгрузки" aria-label="Интеграции и выгрузки" end>
            <Icon name="integrations" /><span>{integrationsPage.label}</span>
          </NavLink>
        </div>
        <div className={`profile${isPageActive(profilePage, pathname) ? ' profile--active' : ''}`}>
          <NavLink className="profile-main" to={profilePage.path} aria-label="Открыть профиль пользователя" title={profile.full_name} end>
            <span className="avatar" aria-hidden="true">{initials}</span>
            <span className="profile-copy">
              <strong>{profile.full_name}</strong>
              <span className="profile-connection"><span className="live-dot" aria-hidden="true" />Подключено</span>
            </span>
          </NavLink>
        </div>
        <div className="nav-utility">
          <button className="nav-item" type="button" onClick={onLogout} disabled={pending} aria-label="Выйти" title="Выйти">
            <Icon name="logout" /><span>Выйти</span>
          </button>
        </div>
      </div>
    </header>
  )
}
