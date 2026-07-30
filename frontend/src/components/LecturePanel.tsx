import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'

export default function LecturePanel({ workspaceId }: { workspaceId: string }) {
  const [topic, setTopic] = useState('')
  const navigate = useNavigate()
  const lectures = useQuery({
    queryKey: ['lectures', workspaceId],
    queryFn: () => api.listLectures(workspaceId),
  })
  const start = useMutation({
    mutationFn: (request: string) => api.createSession(workspaceId).then((session) => ({ session, request })),
    onSuccess: ({ session, request }) => {
      navigate(`/w/${workspaceId}/l/${session.id}`, {
        state: { initial: request, requestId: crypto.randomUUID() },
      })
    },
  })

  return (
    <section className="lecture-library">
      <div className="lecture-library-head">
        <div>
          <h2>Lectures</h2>
          <p className="muted">把资料变成可以中断、提问和跨天继续的互动课程。</p>
        </div>
      </div>
      <form
        className="lecture-start"
        onSubmit={(event) => {
          event.preventDefault()
          const request = topic.trim()
          if (!request) return
          start.mutate(request)
        }}
      >
        <label htmlFor="lecture-topic">今天想系统学习什么？</label>
        <div className="row">
          <input
            id="lecture-topic"
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="例如：给我上一节关于 exponential distribution 的课"
          />
          <button type="submit" disabled={!topic.trim() || start.isPending}>
            {start.isPending ? '正在创建…' : '开始 Lecture'}
          </button>
        </div>
      </form>

      {lectures.isPending ? <p className="muted">正在加载 Lecture…</p> : null}
      {lectures.isError ? <p className="error">{String(lectures.error)}</p> : null}
      {lectures.data?.length === 0 ? (
        <div className="empty">还没有 Lecture。上面输入一个主题就可以开始。</div>
      ) : null}
      <div className="lecture-library-list">
        {lectures.data?.map((lecture) => {
          const total = lecture.total_sections
          const current = Math.min(lecture.current_section_index + 1, total)
          const completed = lecture.status === 'completed' ? total : lecture.current_section_index
          const progress = total ? Math.round((completed / total) * 100) : 0
          return (
            <Link
              className="lecture-library-item"
              key={lecture.id}
              to={`/w/${workspaceId}/l/${lecture.chat_session_id}`}
            >
              <div className="lecture-library-title">
                <strong>{lecture.title}</strong>
                <span className={`lecture-status ${lecture.status}`}>{lecture.status}</span>
              </div>
              <p>{lecture.scope}</p>
              <div className="lecture-progress-label">
                <span>{total ? `第 ${current} / ${total} 节` : '正在准备'}</span>
                <span>{progress}%</span>
              </div>
              <div className="lecture-progress"><span style={{ width: `${progress}%` }} /></div>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
