import { Routes, Route, useParams } from "react-router"
import AppSidebar from "@/components/AppSidebar"
import { Toaster } from "@/components/ui/sonner"
import Home from "@/pages/Home"
import ChatSession from "@/pages/ChatSession"
import KbList from "@/pages/KbList"
import KbDetail from "@/pages/KbDetail"
import Settings from "@/pages/Settings"

/** Remount KbDetail per KB so its page/tree state resets cleanly on nav. */
function KbDetailRoute() {
  const { id = "" } = useParams()
  return <KbDetail key={id} />
}

export default function App() {
  return (
    <div className="h-screen w-screen flex bg-[#e8e8e4] overflow-hidden">
      <AppSidebar />
      <main className="relative flex-1 min-w-0 bg-[#f7f7f4] overflow-hidden">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/chat/:id" element={<ChatSession />} />
          <Route path="/kb" element={<KbList />} />
          <Route path="/kb/:id" element={<KbDetailRoute />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <Toaster />
    </div>
  )
}
