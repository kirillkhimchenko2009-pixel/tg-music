export async function withRequestTimeout(operation, timeoutMs, timeoutMessage) {
  const controller = new AbortController();
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      reject(new Error(timeoutMessage));
      controller.abort();
    }, timeoutMs);
  });
  try {
    return await Promise.race([operation(controller.signal), deadline]);
  } finally {
    clearTimeout(timeout);
  }
}
