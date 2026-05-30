import { useState, useEffect } from 'react'
import { Table, Spinner, Alert, Button, Container } from 'react-bootstrap'
import { Link } from 'react-router-dom'
import { formatDate } from '../utils/formatDate'
import { formatDuration } from '../utils/formatDuration'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export default function SessionsList() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchSessions = async () => {
    try {
      setLoading(true)
      const res = await fetch(`${API_BASE}/api/sessions?limit=100`)
      if (!res.ok) throw new Error('Failed to fetch sessions')
      const data = await res.json()
      setSessions(data.sessions || [])
      setError(null)
    } catch (err) {
      console.error(err)
      setError('Cannot reach the backend API. Is the Flask server running on port 5003?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSessions()
  }, [])

  function getDuration(s) {
    if (!s.start_time) return 0
    const start = new Date(s.start_time)
    if (isNaN(start.getTime())) return 0
    if (s.end_time) {
      const end = new Date(s.end_time)
      if (!isNaN(end.getTime())) {
        const d = (end - start) / 1000
        return d > 0 ? d : 0
      }
    }
    return 0
  }

  return (
    <Container className="py-4">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>Sessions</h2>
        <Button onClick={fetchSessions} variant="outline-primary" size="sm">
          Refresh
        </Button>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      {loading ? (
        <div className="text-center py-5">
          <Spinner animation="border" />
        </div>
      ) : (
        <Table striped bordered hover responsive>
          <thead>
            <tr>
              <th>Session ID</th>
              <th>Car ID</th>
              <th>Started</th>
              <th>Ended</th>
              <th>Duration</th>
              <th>Packets</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 && (
              <tr>
                <td colSpan="7" className="text-center">No sessions found</td>
              </tr>
            )}
            {sessions.map((s) => (
              <tr key={s._id}>
                <td><code>{s._id}</code></td>
                <td>{s.car_ordinal || '—'}</td>
                <td>{formatDate(s.start_time)}</td>
                <td>{s.end_time ? formatDate(s.end_time) : 'Open'}</td>
                <td>{formatDuration(getDuration(s))}</td>
                <td>{s.packet_count || 0}</td>
                <td>
                  <Link to={`/session/${s._id}`} className="btn btn-sm btn-primary">
                    View Details
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <div className="text-muted small mt-3">
        Using Vite proxy (/api → backend)
      </div>
    </Container>
  )
}