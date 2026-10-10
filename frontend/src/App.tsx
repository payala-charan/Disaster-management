import { BrowserRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import Dashboard from "./pages/Dashboard";
import LiveMap from "./pages/LiveMap";
import IncidentCenter from "./pages/IncidentCenter";
import IncidentDetails from "./pages/IncidentDetails";
import ResourceCenter from "./pages/ResourceCenter";
import ShelterManagement from "./pages/ShelterManagement";
import RoutesAccess from "./pages/RoutesAccess";
import ResponsePlanning from "./pages/ResponsePlanning";
import ApprovalCenter from "./pages/ApprovalCenter";
import ActionTracker from "./pages/ActionTracker";
import AlertCenter from "./pages/AlertCenter";
import SituationReports from "./pages/SituationReports";
import Simulation from "./pages/Simulation";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/incidents" element={<IncidentCenter />} />
          <Route path="/incidents/:id" element={<IncidentDetails />} />
          <Route path="/map" element={<LiveMap />} />
          <Route path="/resources" element={<ResourceCenter />} />
          <Route path="/shelters" element={<ShelterManagement />} />
          <Route path="/routes" element={<RoutesAccess />} />
          <Route path="/planning" element={<ResponsePlanning />} />
          <Route path="/approvals" element={<ApprovalCenter />} />
          <Route path="/actions" element={<ActionTracker />} />
          <Route path="/alerts" element={<AlertCenter />} />
          <Route path="/reports" element={<SituationReports />} />
          <Route path="/simulation" element={<Simulation />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
