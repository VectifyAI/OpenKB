import { Routes, Route } from "react-router"

function Placeholder({ label }: { label: string }) {
  return <div className="p-8 text-neutral-500">{label} — wired in a later task</div>
}

export default function App() {
  return (
    <div className="h-screen w-screen flex bg-[#e8e8e4] overflow-hidden">
      <main className="relative flex-1 min-w-0 bg-[#f7f7f4] overflow-hidden">
        <Routes>
          <Route path="/" element={<Placeholder label="Home" />} />
        </Routes>
      </main>
    </div>
  )
}
