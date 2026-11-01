import LiveWorkspace from './LiveWorkspace'

export default function Dashboard() {
  return <div className="app-shell simple-app">
    <header className="simple-header"><div className="brand-symbol">S</div><div><strong>Slotwise</strong><span>Warehouse slotting analysis</span></div></header>
    <main className="simple-main"><LiveWorkspace warehouses={[]} /></main>
  </div>
}
