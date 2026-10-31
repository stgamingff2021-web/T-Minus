import { TasksTable } from "../components/TasksTable";

export default function Tracker() {
  return (
    <div className="page">
      <h1 style={{ marginTop: 0 }}>Action tracker</h1>
      <div className="card"><TasksTable /></div>
    </div>
  );
}
