#!/usr/bin/env python3
"""Simple elastic-plastic stress-strain curve plotter.

수식 (단순 bilinear hardening):

    elastic:   sigma = E * eps                              for eps <= eps_yield
    plastic:   sigma = sigma_y + H * (eps - eps_yield)      for eps >  eps_yield

기본 hardening modulus H = 0.5 * sigma_y / (eps_ultimate - eps_yield),
즉 항복 후 yield 의 50% 만큼 추가 응력 상승하여 ultimate 도달.

호출 예 (wave-5 매니페스트 long_flags 스타일):

    stress_strain_plot.py \\
        --material-name "SUS304" \\
        --e-modulus 200 \\
        --yield-stress 215 \\
        --ultimate-strain 0.40 \\
        --out-path /work/out.png

stdout 으로 JSON 한 줄 반환:
    {"out_path": "...", "material": "...",
     "yield_strain": 0.001075, "max_stress_mpa": 322.5}
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--material-name", required=True, help="표시할 재료 이름 (예: SUS304)")
    p.add_argument("--e-modulus", type=float, required=True, help="탄성계수 (GPa)")
    p.add_argument("--yield-stress", type=float, required=True, help="항복 응력 (MPa)")
    p.add_argument("--ultimate-strain", type=float, default=0.20, help="최대 변형률 (0~1)")
    p.add_argument("--out-path", default="/work/out.png", help="출력 PNG 경로")
    args = p.parse_args()

    # 헤드리스 matplotlib (서버 환경)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    E_Pa = args.e_modulus * 1e9      # GPa -> Pa
    sy_Pa = args.yield_stress * 1e6  # MPa -> Pa
    eps_y = sy_Pa / E_Pa             # 항복 변형률

    if args.ultimate_strain <= eps_y:
        print(json.dumps({
            "error": "ultimate_strain must be greater than yield strain",
            "eps_yield": eps_y,
            "ultimate_strain": args.ultimate_strain,
        }))
        return 2

    # bilinear hardening — 항복 후 sigma_y 의 50% 만큼 추가 상승
    H = 0.5 * sy_Pa / (args.ultimate_strain - eps_y)

    eps = np.linspace(0.0, args.ultimate_strain, 240)
    sigma = np.where(
        eps <= eps_y,
        E_Pa * eps,
        sy_Pa + H * (eps - eps_y),
    )

    # MPa 단위로 표시
    fig, ax = plt.subplots(figsize=(6, 4), dpi=120)
    ax.plot(eps * 100.0, sigma / 1e6, color="#1f77b4", lw=2)
    ax.axvline(eps_y * 100.0, color="gray", ls="--", lw=0.8, alpha=0.6)
    ax.axhline(args.yield_stress, color="gray", ls="--", lw=0.8, alpha=0.6)
    ax.text(eps_y * 100.0, args.yield_stress, f"  yield ({args.yield_stress:.0f} MPa)",
            fontsize=8, va="bottom", ha="left", color="gray")
    ax.set_xlabel("Strain (%)")
    ax.set_ylabel("Stress (MPa)")
    ax.set_title(f"Stress-Strain — {args.material_name}\n"
                 f"E={args.e_modulus:.0f} GPa, σy={args.yield_stress:.0f} MPa")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    os.makedirs(os.path.dirname(args.out_path) or ".", exist_ok=True)
    fig.savefig(args.out_path)
    plt.close(fig)

    print(json.dumps({
        "out_path": args.out_path,
        "material": args.material_name,
        "e_modulus_gpa": args.e_modulus,
        "yield_stress_mpa": args.yield_stress,
        "yield_strain": round(float(eps_y), 6),
        "max_stress_mpa": round(float(sigma.max() / 1e6), 2),
        "ultimate_strain": args.ultimate_strain,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
