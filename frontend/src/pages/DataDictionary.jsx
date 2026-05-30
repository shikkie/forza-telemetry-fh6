import { Container, Table, Card } from 'react-bootstrap'

const FIELDS = [
  { name: 'is_race_on', type: 'bool', desc: 'Whether the car is actively in a race (not in menus)' },
  { name: 'speed', type: 'float (m/s)', desc: 'Vehicle speed in meters per second' },
  { name: 'current_engine_rpm', type: 'float', desc: 'Current engine RPM' },
  { name: 'gear', type: 'int', desc: 'Current gear (0 = Reverse, 1-10 = gears)' },
  { name: 'throttle / brake / clutch / handbrake', type: '0-255', desc: 'Controller input values (normalized 0-1 in frontend)' },
  { name: 'steer', type: '-127 to 127', desc: 'Steering input' },
  { name: 'position_x/y/z', type: 'float (meters)', desc: 'World position of the car' },
  { name: 'tire_combined_slip_*', type: 'float', desc: 'Tire grip loss indicator (|value| > 1.0 = spinning)' },
  { name: 'lap_number / race_position', type: 'int', desc: 'Race progress information' },
  { name: 'power / torque', type: 'float (W / Nm)', desc: 'Engine output' },
]

export default function DataDictionary() {
  return (
    <Container className="py-4">
      <h2>Telemetry Data Dictionary</h2>
      <p className="text-muted">
        This is a quick reference for the main fields coming from the Forza Horizon 6 UDP telemetry packet.
      </p>

      <Card className="mb-4">
        <Card.Body>
          <h5>Key Fields Explained</h5>
          <Table striped bordered hover size="sm">
            <thead>
              <tr>
                <th>Field</th>
                <th>Type</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {FIELDS.map((f, i) => (
                <tr key={i}>
                  <td><code>{f.name}</code></td>
                  <td>{f.type}</td>
                  <td>{f.desc}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      <Card>
        <Card.Body>
          <h5>Notes</h5>
          <ul>
            <li><strong>Car Ordinal</strong>: Unique ID for the car model. Use the <code>cars.py</code> mapping to translate to names.</li>
            <li>Position data is in world meters (not GPS coordinates).</li>
            <li>Tire slip values &gt; 1.0 usually indicate loss of grip.</li>
            <li>Handbrake and clutch are raw 0-255 controller values.</li>
          </ul>
        </Card.Body>
      </Card>
    </Container>
  )
}