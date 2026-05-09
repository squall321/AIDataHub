"""``api.admin`` — 운영 / 관리용 일회성 스크립트 모음.

각 스크립트는 ``python -m api.admin.<name>`` 로 실행하거나 ``main(argv)`` 함수를
직접 호출해 사용한다. 모든 스크립트는 ``--dry-run`` 을 지원하고 멱등(idempotent)
하게 설계된다.
"""
