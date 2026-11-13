#!/usr/bin/env Rscript
# Deterministic R reference generator for T10 — Harvey-Leybourne-Newbold Diebold-Mariano test (D4=B)
# Compatible with forecast::dm.test(e1, e2, h = h, power = 2, varestimator = "acf")

suppressPackageStartupMessages({
  library(jsonlite)
})

dm_hln <- function(e1, e2, h = 1L, power = 2L) {
  n <- length(e1)
  d <- abs(e1)^power - abs(e2)^power
  d_bar <- mean(d)
  u <- d - d_bar
  g0 <- sum(u * u) / n
  d_cov <- g0
  if (h > 1L) {
    for (k in seq_len(h - 1L)) {
      gk <- sum(u[(k + 1L):n] * u[1:(n - k)]) / n
      d_cov <- d_cov + 2.0 * gk
    }
  }
  stat_raw <- d_bar / sqrt(d_cov / n)
  hln <- stat_raw * sqrt((n + 1.0 - 2.0 * h + h * (h - 1.0) / n) / n)
  p_val <- 2.0 * pt(abs(hln), df = n - 1L, lower.tail = FALSE)
  list(statistic = unname(hln), p_value = unname(p_val), df = n - 1L)
}

idx <- seq_len(60L)
e1 <- round(0.80 * sin(idx * 0.79) + 0.35 * cos(idx * 1.91), 8)
# Null equal-accuracy orthogonal error stream with matched mean squared loss
e2_equal <- round(0.80 * cos(idx * 0.79) - 0.35 * sin(idx * 1.91), 8)
# Inferior forecast model with inflated error amplitude
e2_worse <- round(1.65 * sin(idx * 0.79 + 0.4) + 0.85 * cos(idx * 1.37), 8)

res_eq <- dm_hln(e1, e2_equal, h = 1L, power = 2L)
res_worse <- dm_hln(e1, e2_worse, h = 2L, power = 2L)

if (requireNamespace("forecast", quietly = TRUE)) {
  fc <- forecast::dm.test(e1, e2_worse, h = 2L, power = 2L, varestimator = "acf")
  stopifnot(abs(unname(fc$statistic) - res_worse$statistic) < 1e-10)
  stopifnot(abs(unname(fc$p.value) - res_worse$p_value) < 1e-10)
}

cat(toJSON(list(equal_case = res_eq, superior_case = res_worse), auto_unbox = TRUE, digits = 12), "\n")
