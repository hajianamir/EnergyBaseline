#!/usr/bin/env Rscript
# Deterministic R reference generator for T8 — Quandt-Andrews unknown-breakpoint sup-F test (D4=B)
# Compatible with strucchange::Fstats(y ~ x, from = 0.15, to = 0.85)

suppressPackageStartupMessages({
  library(jsonlite)
})

qa_profile <- function(y, x, trim = 0.15) {
  n <- length(y)
  X <- cbind(1.0, x)
  k <- ncol(X)
  t_lo <- max(k + 1L, ceiling(trim * n))
  t_hi <- min(n - k - 1L, floor((1.0 - trim) * n))
  rss_p <- sum(lm.fit(X, y)$residuals^2)
  cands <- seq.int(t_lo, t_hi)
  f_vals <- vapply(cands, function(tb) {
    r1 <- sum(lm.fit(X[1:tb, , drop = FALSE], y[1:tb])$residuals^2)
    r2 <- sum(lm.fit(X[(tb + 1L):n, , drop = FALSE], y[(tb + 1L):n])$residuals^2)
    ru <- r1 + r2
    ((rss_p - ru) / k) / (ru / (n - 2L * k))
  }, numeric(1))
  best <- which.max(f_vals)
  list(
    sup_f = unname(f_vals[best]),
    sup_wald = unname(k * f_vals[best]),
    break_point = unname(cands[best])
  )
}

idx <- seq_len(60L)
x_vec <- round(cos(idx * 0.41) + 0.30 * sin(idx * 0.97), 8)
eps_vec <- round(0.40 * sin(idx * 1.31) + 0.25 * cos(idx * 2.29), 8)

y_null <- round(5.0 + 1.5 * x_vec + eps_vec, 8)
y_break <- round(y_null + ifelse(idx > 36L, 2.8, 0.0), 8)

cat(toJSON(list(null_case = qa_profile(y_null, x_vec), break_case = qa_profile(y_break, x_vec)), auto_unbox = TRUE, digits = 12), "\n")
