#!/usr/bin/env Rscript
# Deterministic R reference generator for T11 — Friedman omnibus & Nemenyi post-hoc OOF rank test (D4=B)
# Compatible with stats::friedman.test and PMCMRplus::frdAllPairsNemenyiTest

suppressPackageStartupMessages({
  library(jsonlite)
})

friedman_nemenyi <- function(L) {
  N <- nrow(L)
  K <- ncol(L)
  R <- t(apply(L, 1L, rank, ties.method = "average"))
  mean_r <- colMeans(R)
  fr <- stats::friedman.test(L)
  se_q <- sqrt(K * (K + 1.0) / (12.0 * N))
  max_q <- max(dist(mean_r)) / se_q
  min_p <- ptukey(max_q, nmeans = K, df = Inf, lower.tail = FALSE)
  list(
    statistic = unname(max_q),
    p_value = unname(min_p),
    friedman_chi2 = unname(fr$statistic),
    friedman_p = unname(fr$p.value)
  )
}

idx <- seq_len(40L)
L_null <- cbind(
  round(1.0 + 0.4 * sin(idx * 1.1), 8),
  round(1.0 + 0.4 * sin(idx * 1.1 + 2.0943951), 8),
  round(1.0 + 0.4 * sin(idx * 1.1 + 4.1887902), 8)
)
L_alt <- cbind(
  round(0.45 + 0.15 * abs(sin(idx * 0.9)), 8),
  round(0.95 + 0.20 * abs(cos(idx * 1.3)), 8),
  round(2.10 + 0.25 * abs(sin(idx * 1.7)), 8)
)

cat(toJSON(list(null_case = friedman_nemenyi(L_null), alt_case = friedman_nemenyi(L_alt)), auto_unbox = TRUE, digits = 12), "\n")
