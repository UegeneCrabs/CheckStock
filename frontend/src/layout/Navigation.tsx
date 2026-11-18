import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { generatePath, NavLink } from 'react-router'
import {
  accessDeniedPage,
  agentPages,
  analyticsGroups,
  arrivalsPage,
  economicsPages,
  isPageActive,
  operationsPage,
  reportPages,
  stockPages,
  yandexFinanceUrl,
} from '../pages'
import type { Page } from '../pages'
import { Icon } from './Icon'
import type { IconName } from './Icon'

type GroupId = 'stock' | 'analytics' | 'unit-1c' | 'reports' | 'finance-reports' | 'agents'

interface NavGroupProps {
  id: GroupId
  icon: IconName
  label: string
  active?: boolean
  open: boolean
  close: () => void
  toggle: () => void
  children: ReactNode
}

function NavGroup({ id, icon, label, active, open, close, toggle, children }: NavGroupProps) {
  const trigger = useRef<HTMLButtonElement>(null)
  const submenuId = `nav-${id}-submenu`

  function handleEscape(event: KeyboardEvent) {
    if (event.key !== 'Escape' || !open) return
    event.stopPropagation()
    close()
    trigger.current?.focus()
  }

  return (
    <div className={`nav-group${open ? ' is-open' : ''}`} onKeyDown={handleEscape}>
      <div className="nav-group-head">
        <button
          ref={trigger}
          className={`nav-item nav-group-trigger${active ? ' active' : ''}${id === 'agents' ? ' agent-nav' : ''}`}
          type="button"
          title={label}
          aria-label={label}
          aria-expanded={open}
          aria-controls={submenuId}
          onClick={toggle}
        >
          <Icon name={icon} />
          <span>{label}</span>
          <Icon name="chevron" />
        </button>
      </div>
      <div id={submenuId} className={id === 'analytics' ? 'nav-submenu nav-analytics-submenu' : 'nav-submenu'} inert={!open}>
        {children}
      </div>
    </div>
  )
}

function PageLinks({ pages, store, report }: { pages: readonly Page[]; store?: string | null; report?: boolean }) {
  return pages.map((page) => {
    const to = page === operationsPage
      ? (store ? generatePath(operationsPage.path, { slug: encodeURIComponent(store) }) : accessDeniedPage.path)
      : page.path
    const classes = report ? `nav-subitem marketplace-report-${page.marketplace}` : 'nav-subitem'
    return <NavLink key={page.path} className={classes} to={to} end>{page.label}</NavLink>
  })
}

const hoverNavigation = '(min-width: 560px) and (hover: hover) and (pointer: fine)'

function AnalyticsBranches({ pathname }: { pathname: string }) {
  const [openBranch, setOpenBranch] = useState<string | null>(null)

  return analyticsGroups.map(({ marketplace, label, pages }) => {
    const id = `nav-analytics-${marketplace}`
    const open = openBranch === id
    const active = pages.some((page) => isPageActive(page, pathname))

    return (
      <div
        key={id}
        className="nav-branch"
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse' && window.matchMedia(hoverNavigation).matches) setOpenBranch(id)
        }}
        onPointerLeave={(event) => {
          if (event.pointerType !== 'mouse' || !window.matchMedia(hoverNavigation).matches) return
          if (event.currentTarget.contains(document.activeElement)) return
          setOpenBranch((current) => current === id ? null : current)
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Escape' || !open) return
          event.stopPropagation()
          setOpenBranch(null)
          event.currentTarget.querySelector('button')?.focus()
        }}
      >
        <button
          className={`nav-subitem nav-branch-trigger${active ? ' active' : ''}`}
          type="button"
          aria-controls={id}
          aria-expanded={open}
          onClick={(event) => {
            const mouseHover = event.detail > 0
              && window.matchMedia(hoverNavigation).matches
              && event.currentTarget.matches(':hover')
            setOpenBranch(open && !mouseHover ? null : id)
          }}
        >
          {label}
          <Icon name="chevron" />
        </button>
        <div id={id} className="nav-branch-links" role="group" aria-label={label} hidden={!open}>
          <PageLinks pages={pages} />
        </div>
      </div>
    )
  })
}

export function Navigation({ pathname, store }: { pathname: string; store: string | null }) {
  const [openGroup, setOpenGroup] = useState<GroupId | null>(null)
  const nav = useRef<HTMLElement>(null)

  useEffect(() => {
    const mobile = window.matchMedia('(max-width: 800px)')
    function closeOnMobile(event: MediaQueryListEvent) {
      if (event.matches) setOpenGroup(null)
    }
    function closeOnOutsideClick(event: PointerEvent) {
      const openMenu = nav.current?.querySelector('.nav-group.is-open')
      if (openMenu && event.target instanceof Node && !openMenu.contains(event.target)) setOpenGroup(null)
    }
    mobile.addEventListener('change', closeOnMobile)
    document.addEventListener('pointerdown', closeOnOutsideClick)
    return () => {
      mobile.removeEventListener('change', closeOnMobile)
      document.removeEventListener('pointerdown', closeOnOutsideClick)
    }
  }, [])

  function group(id: GroupId, groupPages: readonly Page[] = []) {
    return {
      id,
      active: groupPages.some((page) => isPageActive(page, pathname)),
      open: openGroup === id,
      close: () => setOpenGroup(null),
      toggle: () => setOpenGroup((current) => current === id ? null : id),
    }
  }

  return (
    <nav ref={nav} className="side-nav" aria-label="Основная навигация">
      <NavGroup {...group('stock', stockPages)} icon="stock" label="Сток">
        <PageLinks pages={stockPages} store={store} />
      </NavGroup>
      <NavLink className="nav-item" to={arrivalsPage.path} title={arrivalsPage.label} aria-label={arrivalsPage.label} end>
        <Icon name="calendar" />
        <span>{arrivalsPage.label}</span>
      </NavLink>
      <NavGroup {...group('analytics', analyticsGroups.flatMap(({ pages }) => pages))} icon="analytics" label="Аналитика">
        {openGroup === 'analytics' && <AnalyticsBranches pathname={pathname} />}
      </NavGroup>
      <NavGroup {...group('unit-1c', economicsPages)} icon="economics" label="Юнит-экономика 1С">
        <PageLinks pages={economicsPages} />
      </NavGroup>
      <NavGroup {...group('reports', reportPages)} icon="reports" label="Отчёты">
        <PageLinks pages={reportPages} report />
      </NavGroup>
      <NavGroup {...group('finance-reports')} icon="finance" label="Финансовые отчёты">
        <a className="nav-subitem" href={yandexFinanceUrl} target="_blank" rel="noopener noreferrer"
          title="Открыть финансовые отчёты Яндекса в новой вкладке">
          Яндекс
        </a>
      </NavGroup>
      <NavGroup {...group('agents', agentPages)} icon="agents" label="API и интеграции">
        <PageLinks pages={agentPages} />
      </NavGroup>
    </nav>
  )
}
