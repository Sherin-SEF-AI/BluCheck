"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import {
  listVehiclesAdmin, createVehicle, updateVehicle, type VehicleAdmin,
  listUsers, createUser, updateUser, type UserAdmin,
} from "@/lib/api";

export default function ManagePage() {
  const [vehicles, setVehicles] = useState<VehicleAdmin[]>([]);
  const [users, setUsers] = useState<UserAdmin[]>([]);
  const [err, setErr] = useState<string | null>(null);

  // New-vehicle form
  const [vPlate, setVPlate] = useState("");
  const [vModel, setVModel] = useState("");
  // New-user form
  const [uName, setUName] = useState("");
  const [uEmail, setUEmail] = useState("");
  const [uPass, setUPass] = useState("");
  const [uRole, setURole] = useState<"driver" | "admin">("driver");

  const load = useCallback(async () => {
    try {
      const [v, u] = await Promise.all([listVehiclesAdmin(), listUsers()]);
      setVehicles(v);
      setUsers(u);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function addVehicle() {
    setErr(null);
    try {
      await createVehicle({ registration_plate: vPlate, model: vModel || null });
      setVPlate(""); setVModel(""); await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  }
  async function addUser() {
    setErr(null);
    try {
      await createUser({ name: uName, email: uEmail, password: uPass, role: uRole });
      setUName(""); setUEmail(""); setUPass(""); await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  }

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Manage</h2>
        {err ? <div className="error">{err}</div> : null}

        <div className="section-title">VEHICLES</div>
        <div className="filters">
          <input placeholder="Plate (e.g. MH01AB1234)" value={vPlate} onChange={(e) => setVPlate(e.target.value)} />
          <input placeholder="Model" value={vModel} onChange={(e) => setVModel(e.target.value)} />
          <button onClick={addVehicle} disabled={!vPlate.trim()}>Add vehicle</button>
        </div>
        <div className="card" style={{ padding: 0, overflow: "hidden", marginBottom: 24 }}>
          <table>
            <thead><tr><th>Plate</th><th>Model</th><th>Active</th><th></th></tr></thead>
            <tbody>
              {vehicles.map((v) => (
                <tr key={v.id}>
                  <td className="mono">{v.registration_plate}</td>
                  <td>{v.model ?? "-"}</td>
                  <td>{v.active ? "yes" : "no"}</td>
                  <td>
                    <button className="ghost" onClick={async () => { await updateVehicle(v.id, { active: !v.active }); load(); }}>
                      {v.active ? "Deactivate" : "Activate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="section-title">USERS</div>
        <div className="filters">
          <input placeholder="Name" value={uName} onChange={(e) => setUName(e.target.value)} />
          <input placeholder="Email" value={uEmail} onChange={(e) => setUEmail(e.target.value)} />
          <input placeholder="Password (8+)" type="password" value={uPass} onChange={(e) => setUPass(e.target.value)} />
          <select value={uRole} onChange={(e) => setURole(e.target.value as "driver" | "admin")}>
            <option value="driver">driver</option>
            <option value="admin">admin</option>
          </select>
          <button onClick={addUser} disabled={!uName.trim() || !uEmail.trim() || uPass.length < 8}>Add user</button>
        </div>
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th><th></th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td className="mono">{u.email}</td>
                  <td><span className="badge">{u.role}</span></td>
                  <td>{u.active ? "yes" : "no"}</td>
                  <td>
                    <button className="ghost" onClick={async () => { await updateUser(u.id, { active: !u.active }); load(); }}>
                      {u.active ? "Deactivate" : "Activate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
