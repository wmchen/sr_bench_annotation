import { useEffect, useState, type ReactNode } from "react";

interface Shutdown { seconds: number; deadline: number }

/** Keep shutdown independent of login state and stop workspace effects. */
export function ServerLifecycle({children}: {children: ReactNode}) {
  const [shutdown, setShutdown] = useState<Shutdown | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [closeAttempted, setCloseAttempted] = useState(false);

  useEffect(() => {
    const stream = new EventSource("/api/v1/server-events");
    const stop = (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as {countdown_seconds: number};
      if (!Number.isFinite(data.countdown_seconds) || data.countdown_seconds < 0) return;
      stream.close();
      const seconds = data.countdown_seconds;
      setRemaining(seconds);
      setShutdown({seconds, deadline: Date.now() + seconds * 1000});
    };
    stream.addEventListener("shutdown", stop);
    // Network errors retain EventSource's normal reconnect behavior.
    return () => stream.close();
  }, []);

  useEffect(() => {
    if (!shutdown) return;
    let attempted = false;
    const tick = () => {
      const next = Math.max(0, Math.ceil((shutdown.deadline - Date.now()) / 1000));
      setRemaining(next);
      if (next === 0 && !attempted) {
        attempted = true;
        clearInterval(timer);
        setCloseAttempted(true);
        window.close();
      }
    };
    const timer = setInterval(tick, 100);
    tick();
    document.addEventListener("visibilitychange", tick);
    return () => {clearInterval(timer);document.removeEventListener("visibilitychange", tick);};
  }, [shutdown]);

  if (!shutdown) return children;
  return <main className="login shutdown-page">
    <section className="login-card shutdown-card" aria-labelledby="shutdown-title">
      <span className="eyebrow">REAL-ISR / ANNOTATION</span>
      <div className="shutdown-countdown" role="timer" aria-label={`关闭倒计时 ${remaining} 秒`}>
        <svg viewBox="0 0 120 120" aria-hidden="true">
          <circle className="shutdown-track" cx="60" cy="60" r="52"/>
          <circle className="shutdown-progress" cx="60" cy="60" r="52" pathLength="1"
            style={{animationDuration: `${shutdown.seconds}s`}}/>
        </svg>
        <strong>{remaining}</strong>
      </div>
      <h1 id="shutdown-title">服务已停止</h1>
      <p role="status">{closeAttempted
        ? "浏览器未允许自动关闭，请手动关闭此标签页。"
        : `此标签页将在 ${remaining} 秒后尝试自动关闭。`}</p>
      <p className="subtle">工作台已断开连接。</p>
    </section>
  </main>;
}
