#!/usr/bin/env Rscript
# Deterministic R reference generator for Model Confidence Set (MCS) — Hansen, Lunde & Nason (2011) (D4=B)

suppressPackageStartupMessages({
  library(jsonlite)
})

mcs_tmax <- function(L, boot_idx_1based, model_names, alpha = 0.10) {
  M <- ncol(L)
  B <- nrow(boot_idx_1based)
  surviving <- seq_len(M)
  pvals <- setNames(numeric(M), model_names)
  run_max_p <- 0.0
  t_init <- NA_real_

  while (length(surviving) > 1L) {
    Lsub <- L[, surviving, drop = FALSE]
    d_it <- Lsub - rowMeans(Lsub)
    d_bar <- colMeans(d_it)
    b_means <- matrix(0.0, B, length(surviving))
    for (b in seq_len(B)) {
      b_means[b, ] <- colMeans(d_it[boot_idx_1based[b, ], , drop = FALSE]) - d_bar
    }
    se_b <- sqrt(pmax(colMeans(b_means^2), 1e-18))
    t_stats <- d_bar / se_b
    t_obs <- max(t_stats)
    worst <- which.max(t_stats)
    if (is.na(t_init)) t_init <- t_obs
    t_boot <- apply(sweep(b_means, 2L, se_b, "/"), 1L, max)
    p_step <- mean(t_boot >= t_obs - 1e-12)
    run_max_p <- max(run_max_p, p_step)
    pvals[model_names[surviving[worst]]] <- run_max_p
    surviving <- surviving[-worst]
  }
  pvals[model_names[surviving[1L]]] <- 1.0
  sup <- model_names[pvals[model_names] >= alpha]
  list(statistic = unname(t_init), p_value = min(pvals), superior_set = sup, mcs_pvalues = as.list(pvals))
}

T_len <- 36L
idx <- seq_len(T_len)
# Deterministic circular block-bootstrap index matrix (B = 80, block = 3)
B <- 80L
boot_1b <- matrix(0L, B, T_len)
for (b in seq_len(B)) {
  starts <- ((b * 7L + seq_len(12L) * 11L) %% T_len) + 1L
  seq_idx <- as.vector(sapply(starts, function(s) ((s - 1L + 0L:2L) %% T_len) + 1L))
  boot_1b[b, ] <- seq_idx[seq_len(T_len)]
}

L_tie <- cbind(
  round(0.50 + 0.12 * sin(idx * 0.8), 8),
  round(0.51 + 0.12 * cos(idx * 0.8), 8),
  round(0.50 + 0.11 * sin(idx * 0.8 + 1.0), 8)
)
L_dom <- cbind(
  round(0.25 + 0.06 * abs(sin(idx * 0.9)), 8),
  round(0.27 + 0.06 * abs(cos(idx * 0.9)), 8),
  round(1.85 + 0.25 * abs(sin(idx * 1.3)), 8)
)
nms <- c("ols_towt", "gbm_weather", "naive_mean")
cat(toJSON(list(
  tie_case = mcs_tmax(L_tie, boot_1b, nms, 0.10),
  dominated_case = mcs_tmax(L_dom, boot_1b, nms, 0.10)
), auto_unbox = TRUE, digits = 12), "\n")
