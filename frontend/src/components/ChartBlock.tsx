import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ChartPayload } from '../lib/api'

const COLORS = ['#6366f1', '#8b5cf6', '#d946ef', '#22c55e', '#f59e0b', '#38bdf8']

export function ChartBlock({ chart }: { chart: ChartPayload }) {
  const data = chart.points.map((p) => ({ name: p.x, value: p.y }))

  return (
    <div className="chart-block">
      <div className="chart-block-title">
        {chart.type.toUpperCase()} · {chart.x_key} / {chart.y_key}
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height={240}>
          {chart.type === 'pie' ? (
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" outerRadius={90} label>
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          ) : chart.type === 'line' ? (
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="#8b5cf6" strokeWidth={2} />
            </LineChart>
          ) : (
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="value" fill="#6366f1" radius={[6, 6, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
