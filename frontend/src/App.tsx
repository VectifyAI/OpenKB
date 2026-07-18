import { Routes, Route } from "react-router"
import AppSidebar from "@/components/AppSidebar"
import Home from "@/pages/Home"
import KbList from "@/pages/KbList"

export default function App() {
  return (
    <div className="h-screen w-screen flex bg-[#e8e8e4] overflow-hidden">
      <AppSidebar />
      <main className="relative flex-1 min-w-0 bg-[#f7f7f4] overflow-hidden">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/kb" element={<KbList />} />
        </Routes>
      </main>
    </div>
  )
}
