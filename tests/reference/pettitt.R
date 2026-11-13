#!/usr/bin/env Rscript
# Deterministic R reference generator for T9 — Pettitt rank change-point test (D4=B)
# Compatible with trend::pettitt.test(y)

suppressPackageStartupMessages({
  library(jsonlite)
})

pettitt_exact <- function(y) {
  n <- length(y)
  r <- rank(y, ties.method = "average")
  u_t <- 2.0 * cumsum(r[-n]) - seq_len(n - 1L) * (n + 1.0)
  k_pos <- which.max(abs(u_t))
  k_stat <- abs(u_t[k_pos])
  p_val <- min(1.0, 2.0 * exp((-6.0 * k_stat^2) / (n^3 + n^2)))
  list(statistic = unname(k_stat), p_value = unname(p_val), change_point_1based = unname(k_pos))
}

idx <- seq_len(50L)
y_null <- round(sin(idx * 1.73) + 0.45 * cos(idx * 2.61), 8)
y_shift <- round(y_null + ifelse(idx > 25L, 2.25, 0.0), 8)

res_null <- pettitt_exact(y_null)
res_shift <- pettitt_exact(y_shift)

if (requireNamespace("trend", quietly = TRUE)) {
  tr <- trend::pettitt.test(y_shift)
  stopifnot(abs(unname(tr$statistic) - res_shift$statistic) < 1e-10)
  stopifnot(abs(unname(tr$p.value) - res_shift$p_value) < 1e-10)
}

cat(toJSON(list(null_case = res_null, shift_case = res_shift), auto_unbox = TRUE, digits = 12), "\n")
