import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { Layout } from "@/components/Layout"
import { SessionBootstrap } from "@/components/auth/SessionBootstrap"
import { SupportChatPage } from "@/pages/SupportChatPage"
import { SourcesPage } from "@/pages/SourcesPage"
import { CredentialsPage } from "@/pages/CredentialsPage"
import { JobsPage } from "@/pages/JobsPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { DebugPage } from "@/pages/DebugPage"

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

const App = () => (
  <ErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <SessionBootstrap />
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/support" replace />} />
            <Route path="/support" element={<SupportChatPage />} />
            <Route path="/sources" element={<SourcesPage />} />
            <Route path="/credentials" element={<CredentialsPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/debug" element={<DebugPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </ErrorBoundary>
)

export default App
