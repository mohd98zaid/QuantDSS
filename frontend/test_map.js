const history = [
  {"id":1,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:28:27.924875Z","closed_at":null},
  {"id":2,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:30:28.695635Z","closed_at":null},
  {"id":3,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:34:18.227817Z","closed_at":null},
  {"id":4,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:36:35.848204Z","closed_at":null},
  {"id":5,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:42:49.096248Z","closed_at":null},
  {"id":6,"symbol":"LATENCY_B93383","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T14:46:01.304248Z","closed_at":null},
  {"id":7,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:46:11.425585Z","closed_at":null},
  {"id":8,"symbol":"DUP_EEAD37","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T14:46:16.788033Z","closed_at":null},
  {"id":9,"symbol":"LATENCY_8067EF","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T14:55:42.130108Z","closed_at":null},
  {"id":10,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T14:55:52.507617Z","closed_at":null},
  {"id":11,"symbol":"DUP_770459","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T14:55:57.857772Z","closed_at":null},
  {"id":12,"symbol":"LATENCY_65D956","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:16:49.657230Z","closed_at":null},
  {"id":13,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T15:16:59.699203Z","closed_at":null},
  {"id":14,"symbol":"DUP_96871C","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:17:05.021604Z","closed_at":null},
  {"id":15,"symbol":"LATENCY_DE9166","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:20:15.770619Z","closed_at":null},
  {"id":16,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T15:20:25.838542Z","closed_at":null},
  {"id":17,"symbol":"DUP_C48CD7","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:20:31.118749Z","closed_at":null},
  {"id":18,"symbol":"LATENCY_101345","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:22:49.612159Z","closed_at":null},
  {"id":19,"symbol":"RESTART_SYM","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":150.0,"status":"CLOSED","exit_price":null,"realized_pnl":null,"created_at":"2026-03-08T15:22:59.647398Z","closed_at":null},
  {"id":20,"symbol":"DUP_D884FE","instrument_key":null,"direction":"BUY","quantity":10,"entry_price":100.0,"stop_loss":90.0,"target_price":120.0,"status":"CLOSED","exit_price":null,"realized_pnl":0.0,"created_at":"2026-03-08T15:23:07.108950Z","closed_at":null},
  {"id":21,"symbol":"RELIANCE","instrument_key":null,"direction":"BUY","quantity":6,"entry_price":3000.0,"stop_loss":2980.0,"target_price":3040.0,"status":"CLOSED","exit_price":1405.3,"realized_pnl":-9568.2,"created_at":"2026-03-09T07:43:23.630451Z","closed_at":"2026-03-09T07:43:34.370534Z"}
];

try {
  (history || []).map(pos => {
    if (!pos) return;
    const s1 = (pos.symbol || 'UNKNOWN') + '(' + (pos.direction || '-') + ')';
    const s2 = (pos.realized_pnl || 0) > 0 ? 'WIN' : 'LOSS';
    const s3 = '₹' + (pos.entry_price || 0).toFixed(2);
    const s4 = pos.exit_price !== null && pos.exit_price !== undefined ? '₹' + pos.exit_price.toFixed(2) : '-';
    const s5 = ((pos.realized_pnl || 0) >= 0 ? '+' : '') + '₹' + (pos.realized_pnl || 0).toFixed(2);
    //console.log(s1, s2, s3, s4, s5);
  });
  console.log("HISTORY MAP OK");
} catch (e) {
  console.error("HISTORY ERROR:", e);
}
