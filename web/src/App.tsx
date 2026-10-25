import { Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import Interview from "./pages/Interview";
import ProgressPage from "./pages/Progress";
import RepoPage from "./pages/Repo";
import Report from "./pages/Report";
import { Layout } from "./components/ui";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/r/:repoId" element={<RepoPage />} />
      <Route path="/s/:sessionId" element={<Interview />} />
      <Route path="/progress/:repoId" element={<ProgressPage />} />
      <Route path="/report/:token" element={<Report />} />
      <Route
        path="*"
        element={
          <Layout>
            <p className="text-zinc-400">Nothing here.</p>
          </Layout>
        }
      />
    </Routes>
  );
}
