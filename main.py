"""Android(python-for-android) 진입점.

python-for-android 는 ``source.dir`` 루트의 ``main.py`` 를 파이썬 진입 파일로
**고정**한다(``buildozer.spec`` 으로 바꿀 수 없다 — ``android.entrypoint`` 는
자바 Activity 클래스라 별개다). 그래서 앱 본체는 :mod:`photontcp.mobile.app`
에 두고 이 파일은 그것을 부르기만 한다.

데스크톱에서는 ``python -m photontcp.mobile.app`` 도 같은 앱을 띄운다(둘 다
Kivy 설치가 전제다).
"""

from photontcp.mobile.app import main

main()
