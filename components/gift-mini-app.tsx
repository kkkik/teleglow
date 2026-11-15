'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, Gift, Globe2, HelpCircle, Home, LoaderCircle, Package, Search, Sparkles, Trophy, Wallet, X } from 'lucide-react'

type GiftItem = { id: string; emoji: string; stars: number; price: number; out?: boolean }
type State = { balance: number; points: number; streak: number; next_spin: number; win_chance: number; gifts: GiftItem[]; topups: number[]; top: { name: string; points: number }[]; bag: { emoji: string; stars: number; source: string; at: number }[]; deps: { emoji: string; stars: number; credit: number; status: string }[]; ledger: { delta: number; reason: string; at: number }[]; refs: number; support: string; rate: number; auto: boolean; uid: number; bot: string }
type Tg = { initData: string; initDataUnsafe?: { user?: { first_name?: string; language_code?: string } }; ready: () => void; expand: () => void; setHeaderColor?: (color: string) => void; setBackgroundColor?: (color: string) => void; disableVerticalSwipes?: () => void; HapticFeedback?: { impactOccurred: (style: string) => void; notificationOccurred?: (type: string) => void }; openTelegramLink: (url: string) => void; openInvoice: (url: string, callback: (status: string) => void) => void; showConfirm: (message: string, callback: (ok: boolean) => void) => void; BackButton?: { show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void } }
declare global { interface Window { Telegram?: { WebApp?: Tg } } }

type Tab = 'home' | 'shop' | 'wallet' | 'bag' | 'top'
const tabs: { id: Tab; label: string; Icon: typeof Home }[] = [
  { id: 'home', label: 'Home', Icon: Home }, { id: 'shop', label: 'Gifts', Icon: Gift }, { id: 'wallet', label: 'Wallet', Icon: Wallet }, { id: 'bag', label: 'Activity', Icon: Package }, { id: 'top', label: 'Ranks', Icon: Trophy },
]
const call = async (path: string, initData: string, body?: unknown) => {
  const res = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'X-Init': initData, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  const result = await res.json()
  if (!res.ok) throw new Error(result.error || 'Something went wrong')
  return result
}

export function GiftMiniApp() {
  const [tg, setTg] = useState<Tg | null>(null)
  const [data, setData] = useState<State | null>(null)
  const [tab, setTab] = useState<Tab>('home')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState(0)
  const [picked, setPicked] = useState('')

  useEffect(() => {
    const sdk = window.Telegram?.WebApp
    if (!sdk || !sdk.initData) return
    sdk.ready(); sdk.expand()
    try { sdk.setHeaderColor?.('#f7f5f0'); sdk.setBackgroundColor?.('#f7f5f0'); sdk.disableVerticalSwipes?.() } catch {}
    setTg(sdk)
  }, [])

  const refresh = useCallback(async () => {
    if (!tg) return
    setError('')
    try { setData(await call('/api/state', tg.initData)) }
    catch (cause) {
      const code = cause instanceof Error ? cause.message : ''
      setError(code === 'backend_not_configured'
        ? 'Gift service setup is unfinished. Connect the bot backend to load your wallet.'
        : code === 'backend_unavailable'
          ? 'The gift service is offline. Please try again shortly.'
          : 'We couldn’t reach your gift wallet. Check your connection and try again.')
    }
  }, [tg])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { if (!toast) return; const id = window.setTimeout(() => setToast(''), 2600); return () => clearTimeout(id) }, [toast])

  const haptic = (kind = 'light') => { try { tg?.HapticFeedback?.impactOccurred(kind) } catch {} }
  const action = async (path: string, body: unknown) => {
    if (!tg) return null
    setBusy(true)
    try { const result = await call(path, tg.initData, body); await refresh(); return result }
    catch (e) { setToast(e instanceof Error && e.message === 'already_spun' ? 'Your next spin is tomorrow.' : 'That didn’t work. Please try again.'); return null }
    finally { setBusy(false) }
  }
  const filtered = useMemo(() => (data?.gifts || []).filter(g => {
    const ranges = [[0, Infinity], [0, 50], [51, 150], [151, Infinity]][filter]
    return g.stars >= ranges[0] && g.stars <= ranges[1] && `${g.emoji} ${g.stars} ${g.price}`.includes(search.trim())
  }), [data, filter, search])

  if (!tg) return <main className="telegram-gate"><div className="gate-mark"><Gift size={27} /></div><span className="eyebrow">A LITTLE SOMETHING, JUST FOR YOU</span><h1>Gifts feel better<br />in good company.</h1><p>This experience is made exclusively for Telegram. Open it from the bot to explore gifts, collect Stars, and spin your daily reward.</p><div className="gate-note"><span className="gate-dot" /> Telegram Mini App required</div><div className="gate-foot">MADE FOR THE MOMENTS THAT MATTER</div></main>

  const name = tg.initDataUnsafe?.user?.first_name || 'there'
  const spinLeft = data ? Math.max(0, data.next_spin - Date.now() / 1000) : 0
  const spinTimer = `${Math.floor(spinLeft / 3600)}h ${Math.floor((spinLeft % 3600) / 60)}m`
  const doSpin = async () => {
    if (busy || spinLeft > 0) return
    haptic('medium'); const result = await action('/api/spin', {})
    if (result) { tg.HapticFeedback?.notificationOccurred?.('success'); setToast(result.win ? `You won a ${result.emoji} gift!` : `You found ${result.points} points${result.bonus ? ` + ${result.bonus} streak bonus` : ''}!`) }
  }
  const buy = (gift: GiftItem) => {
    if (!data || busy) return
    if (data.balance < gift.price) { setTab('wallet'); setToast('Add Stars to your balance first.'); return }
    tg.showConfirm(`Send ${gift.emoji} for ${gift.price} Stars?`, async ok => {
      if (!ok) return
      const result = await action('/api/buy', { gift_id: gift.id })
      if (result?.ok) setToast(`${result.emoji} is on its way to your Telegram.`)
    })
  }
  const topup = async (amount: number) => {
    if (busy) return
    setBusy(true)
    try {
      const result = await call('/api/topup', tg.initData, { amount })
      if (!result.link) throw new Error('Invoice unavailable')
      tg.openInvoice(result.link, status => { if (status === 'paid') { setToast('Stars added to your balance.'); void refresh() } })
    } catch { setToast('Couldn’t open the Stars invoice. Try again.') }
    finally { setBusy(false) }
  }
  const support = () => { if (data?.support) tg.openTelegramLink(`https://t.me/${data.support}`) }
  const share = () => { if (data) { const link = `https://t.me/${data.bot}?start=ref_${data.uid}`; tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent('A little daily gift, just for you 🎁')}`) } }
  const deposit = async () => { if (!picked) return; const result = await action('/api/deposit', { gift_id: picked }); if (result?.ok) { setPicked(''); setToast(`Request sent · +${result.credit} Stars after confirmation`) } }

  return <main className="gift-app">
    <header className="app-header"><div className="brand"><span className="brand-icon"><Gift size={19} strokeWidth={2.2} /></span><span>little<span className="brand-dot">.</span>gift</span></div><button className="profile-pill" onClick={support} aria-label="Contact support"><span className="avatar">{name.slice(0, 1).toUpperCase()}</span><span>{name}</span><HelpCircle size={15} /></button></header>
    <section className="balance-card"><div className="balance-copy"><span className="eyebrow">YOUR LITTLE TREASURE</span><div className="balance-number">{data ? data.balance.toLocaleString() : '—'} <span>★</span></div><span className="balance-caption">Stars ready for something lovely</span></div><div className="balance-art" aria-hidden="true"><span className="art-spark spark-one">✳</span><span className="art-star">★</span><span className="art-spark spark-two">✦</span><span className="art-orbit" /></div><button className="balance-link" onClick={() => setTab('wallet')}>Add Stars <ArrowUpRight size={15} /></button></section>
    {error && <button className="error-banner" onClick={() => void refresh()}>{error} <span>Tap to retry</span></button>}
    {!data && !error && <div className="loading-line"><LoaderCircle className="spin-icon" size={17} /> Preparing something lovely…</div>}
    {data && <>
      {tab === 'home' && <>
        <section className="welcome-row"><div><span className="eyebrow">A SMALL DAILY DELIGHT</span><h1>Good things<br /><em>find their way.</em></h1></div><div className="welcome-flower" aria-hidden="true">✿</div></section>
        <section className="spin-card"><div className="section-top"><div><span className="eyebrow">YOUR DAILY MOMENT</span><h2>A little luck, daily.</h2></div><span className="streak-pill">✦ {data.streak} day streak</span></div><div className="wheel-row"><div className={`prize-wheel ${busy ? 'is-spinning' : ''}`}><div className="wheel-center"><Sparkles size={23} /><span>LUCK</span></div><i>5</i><i>10</i><i>15</i><i>25</i><i>5</i><i>10</i><i>15</i><i>25</i></div><div className="wheel-note"><span className="chance">{Math.round(data.win_chance * 100)}%</span><span>chance to win<br />a Telegram gift</span></div></div><button className="primary-button spin-button" onClick={() => void doSpin()} disabled={busy || spinLeft > 0}>{busy ? <LoaderCircle className="spin-icon" size={18} /> : <Sparkles size={17} />}{spinLeft > 0 ? `Back in ${spinTimer}` : 'Spin for free'}<span>↗</span></button><div className="spin-foot">FREE ONCE EVERY 24 HOURS <span>STREAK BONUS UP TO +35 POINTS</span></div></section>
        <section className="invite-card"><div className="invite-icon"><ArrowUpRight size={20} /></div><span className="eyebrow">GOOD THINGS ARE BETTER SHARED</span><h2>Send a little joy.</h2><p>Invite a friend. You get 50 points, they get 20. Everyone wins.</p><button className="text-button" onClick={share}>Invite a friend <ArrowUpRight size={15} /></button><span className="invite-count">{data.refs} FRIEND{data.refs === 1 ? '' : 'S'} INVITED</span></section>
        <div className="support-line"><span>Need a hand with a gift?</span><button onClick={support}>We’re right here <ArrowUpRight size={14} /></button></div>
      </>}
      {tab === 'shop' && <><div className="page-intro"><span className="eyebrow">CHOSEN FOR YOU</span><h1>Find your <em>favorite.</em></h1><p>Little treasures, delivered right to your Telegram.</p></div><label className="search-box"><Search size={17} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Find a gift or a price" /><button aria-label="Clear search" onClick={() => setSearch('')}><X size={15} /></button></label><div className="filter-row">{['Everything', 'Under 50 ★', '51–150 ★', '151+ ★'].map((label, i) => <button key={label} onClick={() => setFilter(i)} className={filter === i ? 'filter-active' : ''}>{label}</button>)}</div><div className="shop-hint">A gift sent with a little love <span>PRICES IN STARS</span></div><div className="gift-grid">{filtered.map((g, i) => <article className={`gift-tile gift-tone-${i % 4}`} key={g.id}><div className="gift-tile-top"><span className="gift-emoji">{g.emoji}</span><span className="gift-spark">✳</span></div><span className="gift-value">{g.stars} <span>★</span> value</span><button className="gift-buy" disabled={!!g.out || busy} onClick={() => buy(g)}>{g.out ? 'Out of stock' : <>Send for <b>{g.price} ★</b></>}</button></article>)}{!filtered.length && <div className="empty-state">No gifts found. Try another search.</div>}</div></>}
      {tab === 'wallet' && <><div className="page-intro"><span className="eyebrow">YOUR LITTLE TREASURE</span><h1>Make it <em>sparkle.</em></h1><p>Add Telegram Stars or turn a gift into balance.</p></div><section className="wallet-panel"><div className="panel-heading"><span className="panel-icon"><Wallet size={18} /></span><div><h2>Top up with Stars</h2><p>Securely paid with Telegram Stars</p></div></div><div className="topup-list">{data.topups.map(amount => <button key={amount} disabled={busy} onClick={() => void topup(amount)}><span><span className="star-symbol">★</span>{amount} Stars</span><ArrowUpRight size={16} /></button>)}</div><div className="secure-note"><span>●</span> Payments are handled securely by Telegram</div></section>{data.auto ? <section className="deposit-panel"><span className="eyebrow">AUTO DEPOSIT IS ON</span><h2>Send a gift to @{data.support}</h2><p>Eligible gifts sent to support are detected and credited automatically at {Math.round(data.rate * 100)}% of their value.</p><button className="secondary-button" onClick={support}>Open support <ArrowUpRight size={15} /></button></section> : <section className="wallet-panel deposit-panel"><span className="eyebrow">TURN A GIFT INTO STARS</span><h2>Already sent a gift?</h2><p>Send it to @{data.support}, choose it below, then request a credit. Support will confirm receipt.</p><div className="deposit-grid">{data.gifts.map(g => <button className={picked === g.id ? 'picked' : ''} key={g.id} onClick={() => { setPicked(g.id); haptic() }}>{g.emoji}<small>{g.stars} ★</small></button>)}</div><button className="primary-button deposit-button" disabled={!picked || busy} onClick={() => void deposit()}><ArrowDownToLine size={17} /> Request deposit <span>↗</span></button></section>}</>}
      {tab === 'bag' && <><div className="page-intro"><span className="eyebrow">YOUR STORY SO FAR</span><h1>All the <em>little things.</em></h1><p>Gifts received, deposits, and balance activity.</p></div><History title="Gifts received" empty="Your first gift is waiting." items={data.bag.map(i => ({ icon: i.emoji, label: i.source, detail: new Date(i.at * 1000).toLocaleDateString(), value: `${i.stars} ★` }))} /><History title="Gift deposits" empty="No deposits just yet." items={data.deps.map(i => ({ icon: i.emoji, label: i.status, detail: `${i.stars} Stars value`, value: `+${i.credit} ★` }))} /><History title="Balance history" empty="Your activity will show up here." items={data.ledger.map(i => ({ icon: i.delta > 0 ? '↗' : '↘', label: i.reason, detail: new Date(i.at * 1000).toLocaleDateString(), value: `${i.delta > 0 ? '+' : ''}${i.delta} ★` }))} /></>}
      {tab === 'top' && <><div className="page-intro"><span className="eyebrow">A LITTLE FRIENDLY MAGIC</span><h1>Kindness looks<br /><em>good on you.</em></h1><p>Our community’s top point collectors.</p></div><section className="rank-list">{data.top.map((user, i) => <div className={`rank-row ${i < 3 ? `rank-${i + 1}` : ''}`} key={`${user.name}-${i}`}><span className="rank-number">{String(i + 1).padStart(2, '0')}</span><span className="rank-avatar">{user.name?.slice(0, 1) || '✦'}</span><span className="rank-name">{user.name || 'Telegram friend'}</span><span className="rank-score">{user.points} <small>pts</small></span></div>)}</section></>}
    </>}
    <nav className="bottom-nav" aria-label="Main navigation">{tabs.map(({ id, label, Icon }) => <button key={id} className={tab === id ? 'nav-active' : ''} onClick={() => { setTab(id); haptic() }} aria-current={tab === id ? 'page' : undefined}><Icon size={19} strokeWidth={tab === id ? 2.2 : 1.7} /><span>{label}</span></button>)}</nav>
    {toast && <div className="toast-message" role="status">{toast}</div>}
  </main>
}

function History({ title, empty, items }: { title: string; empty: string; items: { icon: string; label: string; detail: string; value: string }[] }) {
  return <section className="history-section"><div className="history-heading"><h2>{title}</h2><span>{items.length}</span></div>{items.length ? items.map((item, i) => <div className="history-row" key={`${item.label}-${i}`}><span className="history-icon">{item.icon}</span><span className="history-copy"><b>{item.label}</b><small>{item.detail}</small></span><strong>{item.value}</strong></div>) : <div className="history-empty">{empty}</div>}</section>
}
