import { BrowserRouter } from "react-router-dom";
import { AppShell } from "./components/AppShell";

function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

export default App;
