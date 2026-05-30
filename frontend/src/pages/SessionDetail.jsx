import React, { useState, useEffect, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Container, Spinner, Alert, Table, Button, Row, Col, Card } from 'react-bootstrap'
import { formatDate, formatTime } from '../utils/formatDate'
import { usePreferences } from '../utils/usePreferences'
import { convertSpeed, getSpeedUnitLabel } from '../utils/convertSpeed'
import { formatDuration } from '../utils/formatDuration'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export default function SessionDetail() {
  const { sessionId } = useParams()
  const [samples, setSamples] = useState([])
  const [sessionInfo, setSessionInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // New states for enhanced visualization
  const [colorMode, setColorMode] = useState('speed') // 'speed' | 'throttle' | 'handbrake' | 'slip' | 'brakeSlip'
  const [opacityMode, setOpacityMode] = useState('none') // 'none' | 'speed' | 'throttle' | 'handbrake'
  const [hoveredIndex, setHoveredIndex] = useState(null)
  const [selectedIndex, setSelectedIndex] = useState(null) // click-to-inspect on path or table

  const fetchSessionInfo = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`)
      if (!res.ok) throw new Error('Failed to load session')
      const data = await res.json()
      setSessionInfo(data)
      return data
    } catch (err) {
      setError(err.message)
      return null
    }
  }

  const fetchSamples = async (isInitial = false) => {
    try {
      if (isInitial) setLoading(true)
      const res = await fetch(`${API_BASE}/api/telemetry/samples?session_id=${sessionId}&limit=5000`)
      if (!res.ok) throw new Error('Failed to load telemetry samples')
      const data = await res.json()
      const newSamples = data.samples || []

      // Merge for live sessions: keep existing + append only newer ones (by ts)
      setSamples(prev => {
        if (!prev.length || isInitial) return newSamples
        const prevTs = new Set(prev.map(s => s.ts?.$date || s.ts))
        const additions = newSamples.filter(s => !prevTs.has(s.ts?.$date || s.ts))
        return additions.length ? [...prev, ...additions] : prev
      })

      if (isInitial) setError(null)
    } catch (err) {
      if (isInitial) setError(err.message)
    } finally {
      if (isInitial) setLoading(false)
    }
  }

  const fetchData = async () => {
    await fetchSessionInfo()
    await fetchSamples(true)
  }

  useEffect(() => {
    fetchData()
  }, [sessionId])

  // Live polling for open (still-running) sessions so the top-down path grows in real time
  useEffect(() => {
    if (!sessionInfo || sessionInfo.end_time) return undefined

    const interval = setInterval(() => {
      fetchSamples(false) // silent incremental refresh
    }, 1400)

    return () => clearInterval(interval)
  }, [sessionInfo])

  // Click / tap on the top-down path to inspect exact sample packet metrics (speed + steer + brake + grip loss)
  const handlePathClick = (e) => {
    const canvas = canvasRef.current
    const data = pathDataRef.current
    if (!canvas || !data || !data.pathPoints?.length) return

    const rect = canvas.getBoundingClientRect()
    const clickX = e.clientX - rect.left
    const clickY = e.clientY - rect.top

    let closest = null
    let minDist = Infinity
    let bestOriginalIdx = null

    for (const p of data.pathPoints) {
      const px = data.scaleX(p.x)
      const py = data.scaleZ(p.z)
      const dx = px - clickX
      const dy = py - clickY
      const dist = dx * dx + dy * dy
      if (dist < minDist) {
        minDist = dist
        closest = p
        bestOriginalIdx = p.originalIdx
      }
    }

    if (closest && minDist < 1200) {
      setSelectedIndex(bestOriginalIdx)
      const reversedForTable = samples.length - 1 - bestOriginalIdx
      setHoveredIndex(reversedForTable)
      setTimeout(() => setHoveredIndex(null), 1100)
    } else {
      setSelectedIndex(null)
    }
  }

  const handlePathMouseMove = (e) => {
    const canvas = canvasRef.current
    const data = pathDataRef.current
    if (!canvas || !data || !data.pathPoints?.length) return

    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    let closest = null
    let minDist = Infinity
    let bestOriginalIdx = null

    for (const p of data.pathPoints) {
      const px = data.scaleX(p.x)
      const py = data.scaleZ(p.z)
      const dx = px - mx
      const dy = py - my
      const dist = dx * dx + dy * dy
      if (dist < minDist) {
        minDist = dist
        closest = p
        bestOriginalIdx = p.originalIdx
      }
    }

    if (closest && minDist < 900) {
      const reversed = samples.length - 1 - bestOriginalIdx
      if (hoveredIndex !== reversed) setHoveredIndex(reversed)
    }
  }

  const handlePathMouseLeave = () => {
    setHoveredIndex(null)
  }

  const { speed_unit, loading: prefsLoading } = usePreferences()

  // Compute aggregates
  const stats = React.useMemo(() => {
    if (!samples.length) return null

    const speeds = samples.map(s => s.speed || 0)
    const rpms = samples.map(s => s.current_engine_rpm || 0)
    const slips = samples.flatMap(s => [
      s.tire_slip_fl || 0, s.tire_slip_fr || 0,
      s.tire_slip_rl || 0, s.tire_slip_rr || 0
    ])
    const throttles = samples.map(s => s.throttle || 0)
    const brakes = samples.map(s => s.brake || 0)
    const handbrakes = samples.map(s => s.handbrake || 0)

    const avg = arr => arr.reduce((a, b) => a + b, 0) / arr.length
    const max = arr => Math.max(...arr)

    // Prefer session metadata for accurate total duration
    let duration = 0

    if (sessionInfo) {
      const start = sessionInfo.start_time ? new Date(sessionInfo.start_time) : null
      const end = sessionInfo.end_time ? new Date(sessionInfo.end_time) : null

      if (start && !isNaN(start.getTime())) {
        if (end && !isNaN(end.getTime())) {
          duration = (end - start) / 1000
        } else {
          // Open session: use the latest sample we have
          const latest = samples[0]?.ts ? new Date(samples[0].ts) : null
          if (latest && !isNaN(latest.getTime())) {
            duration = (latest - start) / 1000
          }
        }
      }
    }

    // Fallback only if we have no session times at all
    if (duration === 0 && samples.length > 1) {
      const first = new Date(samples[samples.length - 1].ts)   // oldest in our batch (because sorted desc)
      const last = new Date(samples[0].ts)                     // newest
      if (!isNaN(first.getTime()) && !isNaN(last.getTime())) {
        duration = (last - first) / 1000
      }
    }

    const handbrakeOnCount = handbrakes.filter(h => h > 50).length

    // Total distance (very rough approximation from position data)
    let totalDistance = 0
    for (let i = 1; i < samples.length; i++) {
      const dx = (samples[i].position_x || 0) - (samples[i - 1].position_x || 0)
      const dz = (samples[i].position_z || 0) - (samples[i - 1].position_z || 0)
      totalDistance += Math.sqrt(dx * dx + dz * dz)
    }

    // Grip loss events (any tire slip > 1.0)
    const gripLossEvents = samples.filter(s =>
      (s.tire_slip_fl > 1 || s.tire_slip_fr > 1 ||
       s.tire_slip_rl > 1 || s.tire_slip_rr > 1)
    ).length

    // Simple oversteer proxy
    const oversteerEvents = samples.filter(s => {
      const front = Math.max(s.tire_slip_fl || 0, s.tire_slip_fr || 0)
      const rear = Math.max(s.tire_slip_rl || 0, s.tire_slip_rr || 0)
      return rear > front + 0.3 && rear > 0.8
    }).length

    // Time-based metrics (estimate using average sample interval)
    const sampleDuration = duration / samples.length || 0.0167

    const timeAbove100Mph = samples.filter(s => s.speed > 44.7).length * sampleDuration
    const timeAbove150Mph = samples.filter(s => s.speed > 67.05).length * sampleDuration

    return {
      avgSpeed: avg(speeds),
      maxSpeed: max(speeds),
      avgRpm: avg(rpms),
      maxRpm: max(rpms),
      avgTireSlip: avg(slips),
      maxTireSlip: max(slips),
      avgThrottle: avg(throttles) * 100,
      avgBrake: avg(brakes) * 100,
      handbrakeOnPercent: (handbrakeOnCount / samples.length) * 100,
      sampleCount: samples.length,
      duration: isFinite(duration) && duration > 0 ? duration : 0,
      totalDistance: totalDistance.toFixed(0),
      gripLossEvents,
      oversteerEvents,
      timeAbove100Mph: timeAbove100Mph.toFixed(1),
      timeAbove150Mph: timeAbove150Mph.toFixed(1),
      maxSingleTireSlip: Math.max(
        ...samples.flatMap(s => [
          s.tire_slip_fl || 0,
          s.tire_slip_fr || 0,
          s.tire_slip_rl || 0,
          s.tire_slip_rr || 0
        ])
      ),
    }
  }, [samples, sessionInfo])

  // Proper reactive path drawing
  const canvasRef = useRef(null)
  const pathDataRef = useRef(null) // { pathPoints, positions, scaleX, scaleZ, minX, maxX, minZ, maxZ, padding } for hit testing + live updates

  // Enhanced path drawing with color mode + hover highlight + brake+slip markers + click support
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !samples.length) return

    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const positions = samples
      .filter(s => s.position_x != null && s.position_z != null)
      .map((s, originalIdx) => ({
        x: s.position_x,
        z: s.position_z,
        speed: s.speed || 0,
        throttle: s.throttle || 0,
        brake: s.brake || 0,
        handbrake: s.handbrake || 0,
        tireSlip: Math.max(s.tire_slip_fl || 0, s.tire_slip_fr || 0, s.tire_slip_rl || 0, s.tire_slip_rr || 0),
        steer: s.steer || 0,
        _ts: s.ts,
        originalIdx, // preserve mapping back to samples array
      }))

    if (positions.length < 2) return

    // Chronological order for beautiful path tracing (oldest → newest)
    const pathPoints = [...positions].sort((a, b) => {
      const ta = new Date(a._ts?.$date || a._ts).getTime()
      const tb = new Date(b._ts?.$date || b._ts).getTime()
      return ta - tb
    })

    const xs = pathPoints.map(p => p.x)
    const zs = pathPoints.map(p => p.z)

    const minX = Math.min(...xs), maxX = Math.max(...xs)
    const minZ = Math.min(...zs), maxZ = Math.max(...zs)

    const padding = 20
    const w = canvas.width - padding * 2
    const h = canvas.height - padding * 2

    const scaleX = (x) => padding + ((x - minX) / (maxX - minX || 1)) * w
    const scaleZ = (z) => padding + ((z - minZ) / (maxZ - minZ || 1)) * h

    // Store for hit-testing on click / future live interactions
    pathDataRef.current = { pathPoints, positions, scaleX, scaleZ, minX, maxX, minZ, maxZ, padding, w, h }

    // Color helper — now supports slip and brake+slip for "seeing braking tire slip data"
    const getColor = (p) => {
      if (colorMode === 'slip') {
        const v = Math.min(p.tireSlip * 0.8, 1) // scale for visibility
        if (v < 0.25) return '#2ecc71'
        if (v < 0.55) return '#f1c40f'
        return '#e74c3c'
      }
      if (colorMode === 'brakeSlip') {
        const brakeSeverity = Math.max(p.brake, p.handbrake / 200)
        const combined = brakeSeverity * Math.min(p.tireSlip * 1.2, 1.5)
        if (combined > 0.6) return '#c0392b' // strong braking + losing grip
        if (combined > 0.25) return '#e67e22'
        return '#3498db'
      }

      let value = 0
      if (colorMode === 'speed') value = p.speed
      else if (colorMode === 'throttle') value = p.throttle * 100
      else if (colorMode === 'handbrake') value = p.handbrake

      if (colorMode === 'handbrake') {
        return value > 50 ? '#e74c3c' : '#2ecc71'
      }
      if (value < 30) return '#2ecc71'
      if (value < 60) return '#f1c40f'
      return '#e74c3c'
    }

    const getOpacity = (p) => {
      if (opacityMode === 'none') return 1
      let val = 0
      if (opacityMode === 'speed') val = Math.min(p.speed / 80, 1)
      else if (opacityMode === 'throttle') val = p.throttle
      else if (opacityMode === 'handbrake') val = p.handbrake / 255
      return 0.3 + val * 0.7
    }

    // Draw the path in drive order
    for (let i = 1; i < pathPoints.length; i++) {
      const p1 = pathPoints[i - 1]
      const p2 = pathPoints[i]

      const x1 = scaleX(p1.x)
      const y1 = scaleZ(p1.z)
      const x2 = scaleX(p2.x)
      const y2 = scaleZ(p2.z)

      const alpha = getOpacity(p2)
      ctx.strokeStyle = getColor(p2)
      ctx.globalAlpha = alpha
      ctx.lineWidth = 2.5 + (opacityMode !== 'none' ? alpha * 1.5 : 0)
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()
      ctx.globalAlpha = 1

      // Extra visual marker for braking + tire slip events (the key request)
      const brakeNow = p2.brake > 0.25 || p2.handbrake > 60
      if (brakeNow && p2.tireSlip > 0.6) {
        ctx.fillStyle = '#c0392b'
        ctx.beginPath()
        ctx.arc(x2, y2, 3.5, 0, Math.PI * 2)
        ctx.fill()
        // small "loss of grip" indicator lines
        ctx.strokeStyle = '#c0392b'
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.moveTo(x2 - 5, y2 - 5)
        ctx.lineTo(x2 + 5, y2 + 5)
        ctx.stroke()
      }
    }

    // Hovered marker (from timeline or speed graph)
    if (hoveredIndex !== null) {
      const idx = samples.length - 1 - hoveredIndex
      const match = positions.find(pp => pp.originalIdx === idx)
      if (match) {
        const hx = scaleX(match.x)
        const hy = scaleZ(match.z)
        ctx.fillStyle = '#ffffff'
        ctx.strokeStyle = '#000000'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.arc(hx, hy, 6, 0, Math.PI * 2)
        ctx.fill()
        ctx.stroke()
      }
    }

    // Selected (clicked) marker — bigger, persistent, cyan
    if (selectedIndex !== null) {
      const match = positions.find(pp => pp.originalIdx === selectedIndex)
      if (match) {
        const sx = scaleX(match.x)
        const sy = scaleZ(match.z)
        ctx.fillStyle = '#00f0ff'
        ctx.strokeStyle = '#003366'
        ctx.lineWidth = 2.5
        ctx.beginPath()
        ctx.arc(sx, sy, 8, 0, Math.PI * 2)
        ctx.fill()
        ctx.stroke()
        // crosshair
        ctx.strokeStyle = '#003366'
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(sx - 10, sy)
        ctx.lineTo(sx + 10, sy)
        ctx.moveTo(sx, sy - 10)
        ctx.lineTo(sx, sy + 10)
        ctx.stroke()
      }
    }
  }, [samples, colorMode, opacityMode, hoveredIndex, selectedIndex])

  if (loading) {
    return <div className="text-center py-5"><Spinner animation="border" /></div>
  }

  if (error) {
    return <Alert variant="danger">{error}</Alert>
  }

  return (
    <Container className="py-4">
      <div className="d-flex justify-content-between mb-3 align-items-center">
        <h2 className="mb-0">
          Session {sessionId}
          {sessionInfo && !sessionInfo.end_time && (
            <span className="badge bg-danger ms-2 align-middle" style={{ fontSize: '0.6em', verticalAlign: 'middle' }}>LIVE</span>
          )}
        </h2>
        <div>
          <Link to="/" className="btn btn-outline-secondary me-2">← Back to Sessions</Link>
          <Button 
            variant="outline-success" 
            size="sm"
            onClick={() => exportToCSV(samples, sessionId)}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {sessionInfo && (
        <Card className="mb-4">
          <Card.Body>
            <Row>
              <Col md={3}><strong>Car Ordinal:</strong> {sessionInfo.car_ordinal}</Col>
              <Col md={3}><strong>Started:</strong> {formatDate(sessionInfo.start_time)}</Col>
              <Col md={3}><strong>Ended:</strong> {formatDate(sessionInfo.end_time)}</Col>
              <Col md={3}><strong>Duration:</strong> {formatDuration(stats?.duration)}</Col>
            </Row>
          </Card.Body>
        </Card>
      )}

      {/* Aggregates */}
      {stats && !prefsLoading && (
        <Row className="mb-4 g-3">
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Avg Speed</div>
                <h4>{convertSpeed(stats.avgSpeed, speed_unit)} {getSpeedUnitLabel(speed_unit)}</h4>
                <div className="text-muted small">
                  Max: {convertSpeed(stats.maxSpeed, speed_unit)} {getSpeedUnitLabel(speed_unit)}
                </div>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Avg RPM</div>
                <h4>{Math.round(stats.avgRpm)}</h4>
                <div className="text-muted small">Max: {Math.round(stats.maxRpm)}</div>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Avg Tire Slip</div>
                <h4>{stats.avgTireSlip.toFixed(3)}</h4>
                <div className="text-muted small">Max: {stats.maxTireSlip.toFixed(2)}</div>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Throttle / Brake / Handbrake</div>
                <div>Avg Throttle: {stats.avgThrottle.toFixed(1)}%</div>
                <div>Avg Brake: {stats.avgBrake.toFixed(1)}%</div>
                <div>Handbrake On: {stats.handbrakeOnPercent.toFixed(1)}% of time</div>
              </Card.Body>
            </Card>
          </Col>
        </Row>
      )}

      {/* Extra Aggregates */}
      {stats && (
        <Row className="mb-4 g-3">
          <Col md={4}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Total Distance (approx)</div>
                <h4>{stats.totalDistance} m</h4>
              </Card.Body>
            </Card>
          </Col>
          <Col md={4}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Grip Loss Events</div>
                <h4>{stats.gripLossEvents}</h4>
                <div className="text-muted small">Samples with any tire slip &gt; 1.0</div>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Max Single Tire Slip</div>
                <h4>{stats.maxSingleTireSlip?.toFixed(2) || '—'}</h4>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Oversteer Events (proxy)</div>
                <h4>{stats.oversteerEvents}</h4>
                <div className="text-muted small">Rear slip significantly &gt; front</div>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Time &gt; 100 mph</div>
                <h4>{stats.timeAbove100Mph} s</h4>
              </Card.Body>
            </Card>
          </Col>
          <Col md={3}>
            <Card bg="light">
              <Card.Body>
                <div className="text-muted small">Time &gt; 150 mph</div>
                <h4>{stats.timeAbove150Mph} s</h4>
              </Card.Body>
            </Card>
          </Col>
        </Row>
      )}

      {/* Improved Path Plot with color modes */}
      <Card className="mb-4">
        <Card.Header className="d-flex justify-content-between align-items-center flex-wrap gap-2">
          <span>Path (Top-down view)</span>

          <div className="d-flex align-items-center gap-2">
            {/* Color Mode - including new brake+slip / grip modes */}
            <div>
              <small className="me-1 text-muted">Color:</small>
              <Button size="sm" variant={colorMode === 'speed' ? 'primary' : 'outline-primary'} className="me-1" onClick={() => setColorMode('speed')}>Speed</Button>
              <Button size="sm" variant={colorMode === 'throttle' ? 'primary' : 'outline-primary'} className="me-1" onClick={() => setColorMode('throttle')}>Throttle</Button>
              <Button size="sm" variant={colorMode === 'handbrake' ? 'primary' : 'outline-primary'} onClick={() => setColorMode('handbrake')}>Handbrake</Button>
              <Button size="sm" variant={colorMode === 'slip' ? 'primary' : 'outline-primary'} onClick={() => setColorMode('slip')}>Slip</Button>
              <Button size="sm" variant={colorMode === 'brakeSlip' ? 'primary' : 'outline-primary'} onClick={() => setColorMode('brakeSlip')}>Brake+Slip</Button>
            </div>

            {/* Opacity / Line Width Mode */}
            <div>
              <small className="me-1 text-muted">Opacity:</small>
              <Button size="sm" variant={opacityMode === 'none' ? 'primary' : 'outline-primary'} className="me-1" onClick={() => setOpacityMode('none')}>None</Button>
              <Button size="sm" variant={opacityMode === 'speed' ? 'primary' : 'outline-primary'} className="me-1" onClick={() => setOpacityMode('speed')}>Speed</Button>
              <Button size="sm" variant={opacityMode === 'throttle' ? 'primary' : 'outline-primary'} className="me-1" onClick={() => setOpacityMode('throttle')}>Throttle</Button>
              <Button size="sm" variant={opacityMode === 'handbrake' ? 'primary' : 'outline-primary'} onClick={() => setOpacityMode('handbrake')}>Handbrake</Button>
            </div>
          </div>
        </Card.Header>
        <Card.Body>
          <canvas
            ref={canvasRef}
            width={800}
            height={500}
            style={{ border: '1px solid #ddd', width: '100%', maxWidth: '800px', cursor: 'crosshair' }}
            onClick={handlePathClick}
            onMouseMove={handlePathMouseMove}
            onMouseLeave={handlePathMouseLeave}
          />
          <div className="text-muted small mt-2">
            Color: {colorMode} &nbsp;|&nbsp; Opacity/Thickness: {opacityMode}. 
            Click anywhere on the track to inspect that exact sample's speed + steer + brake + tire slip metrics. 
            "Brake+Slip" mode highlights where braking causes grip loss (red dots + X marks).
          </div>
          {samples.length > 0 && !samples.some(s => s.position_x != null) && (
            <div className="text-warning small mt-1">
              No position data in this session (recorded before top-down path support was enabled). Drive a new session to see the map.
            </div>
          )}
        </Card.Body>
      </Card>

      {/* Live / clicked sample packet details — shows speed, steer, brake, all tire slips, grip etc. */}
      {(selectedIndex !== null || hoveredIndex !== null) && samples.length > 0 && (
        <Card className="mb-4 border-info">
          <Card.Header className="bg-info text-white py-1">
            <strong>
              {selectedIndex !== null ? 'Selected Point on Track' : 'Hovered Sample'}
              {' — '}Packet Metrics
            </strong>
            {selectedIndex !== null && (
              <Button size="sm" variant="light" className="ms-2 py-0 px-1" onClick={() => setSelectedIndex(null)}>
                Clear
              </Button>
            )}
          </Card.Header>
          <Card.Body className="py-2">
            {(() => {
              const idx = selectedIndex !== null ? selectedIndex : (samples.length - 1 - hoveredIndex)
              const s = samples[idx]
              if (!s) return <div className="text-muted">No data</div>

              const maxSlip = Math.max(s.tire_slip_fl || 0, s.tire_slip_fr || 0, s.tire_slip_rl || 0, s.tire_slip_rr || 0)
              const isBrakingHard = (s.brake || 0) > 0.3 || (s.handbrake || 0) > 80
              const gripLoss = maxSlip > 0.8

              return (
                <Row className="small">
                  <Col md={3}>
                    <strong>Time:</strong> {formatTime(s.ts)}<br />
                    <strong>Speed:</strong> {(s.speed || 0).toFixed(1)} m/s<br />
                    <strong>RPM:</strong> {Math.round(s.current_engine_rpm || 0)}
                  </Col>
                  <Col md={3}>
                    <strong>Steer:</strong> {((s.steer || 0) * 100).toFixed(0)}%<br />
                    <strong>Throttle:</strong> {((s.throttle || 0) * 100).toFixed(0)}%<br />
                    <strong>Brake:</strong> {((s.brake || 0) * 100).toFixed(0)}%
                    {isBrakingHard && <span className="text-danger fw-bold"> HARD</span>}
                  </Col>
                  <Col md={3}>
                    <strong>Tire Slips (FL/FR/RL/RR):</strong><br />
                    {((s.tire_slip_fl || 0)).toFixed(2)} / {((s.tire_slip_fr || 0)).toFixed(2)} / {((s.tire_slip_rl || 0)).toFixed(2)} / {((s.tire_slip_rr || 0)).toFixed(2)}<br />
                    <span className={gripLoss ? 'text-danger fw-bold' : ''}>
                      Max slip: {maxSlip.toFixed(2)} {gripLoss ? '← GRIP LOSS' : ''}
                    </span>
                  </Col>
                  <Col md={3}>
                    <strong>Handbrake:</strong> {s.handbrake > 50 ? <span className="text-danger">ON</span> : '—'}<br />
                    <strong>Yaw:</strong> {s.yaw ? (s.yaw * 57.3).toFixed(1) + '°' : '—'}<br />
                    <strong>Pos:</strong> ({(s.position_x || 0).toFixed(0)}, {(s.position_z || 0).toFixed(0)})
                    {isBrakingHard && gripLoss && <div className="text-danger mt-1"><strong>Braking + tire slip detected</strong></div>}
                  </Col>
                </Row>
              )
            })()}
          </Card.Body>
        </Card>
      )}

      {/* Speed over Time Graph */}
      <Card className="mb-4">
        <Card.Header>Speed Over Time</Card.Header>
        <Card.Body>
          <SpeedGraph 
            samples={samples} 
            speedUnit={speed_unit} 
            hoveredIndex={hoveredIndex}
            onHover={setHoveredIndex}
          />
          <div className="text-muted small mt-2">
            Simple line graph of speed throughout the session.
          </div>
        </Card.Body>
      </Card>

      {/* Timeline Scrubber */}
      <Card>
        <Card.Header>Timeline (last {samples.length} samples)</Card.Header>
        <Card.Body style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <Table striped size="sm">
            <thead>
              <tr>
                <th>Time</th>
                <th>Speed (m/s)</th>
                <th>RPM</th>
                <th>Gear</th>
                <th>Throttle</th>
                <th>Brake</th>
                <th>Handbrake</th>
              </tr>
            </thead>
            <tbody>
              {samples.slice().reverse().map((s, idx) => {
                const reversedIdx = samples.length - 1 - idx
                const isHovered = hoveredIndex === reversedIdx

                return (
                  <tr
                    key={idx}
                    style={{ backgroundColor: isHovered ? '#fff3cd' : 'transparent', cursor: 'pointer' }}
                    onMouseEnter={() => setHoveredIndex(reversedIdx)}
                    onMouseLeave={() => setHoveredIndex(null)}
                    onClick={() => {
                      setSelectedIndex(reversedIdx)
                      setHoveredIndex(null)
                    }}
                  >
                    <td>{formatTime(s.ts)}</td>
                    <td>{(s.speed || 0).toFixed(1)}</td>
                    <td>{Math.round(s.current_engine_rpm || 0)}</td>
                    <td>{s.gear}</td>
                    <td>{((s.throttle || 0) * 100).toFixed(0)}%</td>
                    <td>{((s.brake || 0) * 100).toFixed(0)}%</td>
                    <td style={{ color: s.handbrake > 50 ? 'red' : 'inherit', fontWeight: isHovered ? 'bold' : 'normal' }}>
                      {s.handbrake > 50 ? 'ON' : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </Container>
  )
}

// Interactive speed-over-time graph with throttle/brake overlays
function SpeedGraph({ samples, speedUnit, hoveredIndex, onHover }) {
  const canvasRef = useRef(null)

  const draw = (hoveredIdx) => {
    const canvas = canvasRef.current
    if (!canvas || !samples.length) return

    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const speeds = samples.map(s => s.speed || 0)
    const throttles = samples.map(s => (s.throttle || 0) * 100)
    const brakes = samples.map(s => (s.brake || 0) * 100)

    const maxSpeed = Math.max(...speeds, 1)

    const padding = 30
    const w = canvas.width - padding * 2
    const h = canvas.height - padding * 2

    // Draw axes
    ctx.strokeStyle = '#666'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(padding, padding)
    ctx.lineTo(padding, canvas.height - padding)
    ctx.lineTo(canvas.width - padding, canvas.height - padding)
    ctx.stroke()

    // Draw throttle overlay (light green)
    ctx.strokeStyle = 'rgba(46, 204, 113, 0.6)'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    samples.forEach((s, i) => {
      const x = padding + (i / (samples.length - 1)) * w
      const y = canvas.height - padding - (throttles[i] / 100) * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()

    // Draw brake overlay (light red)
    ctx.strokeStyle = 'rgba(231, 76, 60, 0.6)'
    ctx.beginPath()
    samples.forEach((s, i) => {
      const x = padding + (i / (samples.length - 1)) * w
      const y = canvas.height - padding - (brakes[i] / 100) * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()

    // Draw main speed line
    ctx.strokeStyle = '#3498db'
    ctx.lineWidth = 2.5
    ctx.beginPath()
    samples.forEach((s, i) => {
      const x = padding + (i / (samples.length - 1)) * w
      const y = canvas.height - padding - ((s.speed || 0) / maxSpeed) * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()

    // Draw hover marker
    if (hoveredIdx !== null && hoveredIdx >= 0 && hoveredIdx < samples.length) {
      const x = padding + (hoveredIdx / (samples.length - 1)) * w
      const y = canvas.height - padding - ((samples[hoveredIdx].speed || 0) / maxSpeed) * h

      ctx.fillStyle = '#e74c3c'
      ctx.beginPath()
      ctx.arc(x, y, 5, 0, Math.PI * 2)
      ctx.fill()

      ctx.strokeStyle = '#e74c3c'
      ctx.setLineDash([4, 2])
      ctx.beginPath()
      ctx.moveTo(x, padding)
      ctx.lineTo(x, canvas.height - padding)
      ctx.stroke()
      ctx.setLineDash([])
    }

    // Legend
    ctx.fillStyle = '#333'
    ctx.font = '11px sans-serif'
    ctx.fillText(`Speed (${getSpeedUnitLabel(speedUnit)})`, canvas.width - 120, 18)
    ctx.fillStyle = '#2ecc73'
    ctx.fillText('Throttle', canvas.width - 120, 32)
    ctx.fillStyle = '#e74c3c'
    ctx.fillText('Brake', canvas.width - 120, 46)
  }

  useEffect(() => {
    draw(hoveredIndex)
  }, [samples, speedUnit, hoveredIndex])

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current
    if (!canvas || !samples.length) return

    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    const padding = 30
    const w = canvas.width - padding * 2

    const progress = Math.max(0, Math.min(1, (x - padding) / w))
    const idx = Math.floor(progress * (samples.length - 1))

    if (onHover) onHover(idx)
  }

  const handleMouseLeave = () => {
    if (onHover) onHover(null)
  }

  return (
    <canvas
      ref={canvasRef}
      width={800}
      height={200}
      style={{ border: '1px solid #ddd', width: '100%', maxWidth: '800px', cursor: 'crosshair' }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    />
  )
}

// CSV Export helper
function exportToCSV(samples, sessionId) {
  if (!samples || samples.length === 0) return

  const headers = ['time', 'speed_mps', 'rpm', 'gear', 'throttle', 'brake', 'handbrake', 'steer', 'tire_slip_fl', 'tire_slip_fr', 'tire_slip_rl', 'tire_slip_rr', 'yaw', 'pos_x', 'pos_z']
  
  const rows = samples.map(s => [
    s.ts,
    s.speed ?? '',
    s.current_engine_rpm ?? '',
    s.gear ?? '',
    s.throttle ?? '',
    s.brake ?? '',
    s.handbrake ?? '',
    s.steer ?? '',
    s.tire_slip_fl ?? '',
    s.tire_slip_fr ?? '',
    s.tire_slip_rl ?? '',
    s.tire_slip_rr ?? '',
    s.yaw ?? '',
    s.position_x ?? '',
    s.position_z ?? '',
  ])

  let csvContent = headers.join(',') + '\n'
  rows.forEach(row => {
    csvContent += row.join(',') + '\n'
  })

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `session_${sessionId}_telemetry.csv`
  link.click()
  URL.revokeObjectURL(url)
}