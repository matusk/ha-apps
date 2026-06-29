import re
from datetime import datetime

html = open('page.html').read()
hdo_code = '259'

# More strict regex to stop at the end of the array
pattern = re.compile(rf"code:\s*['\"]?{hdo_code}['\"]?,\s*intervals:\s*\[(.*?)\]", re.DOTALL | re.IGNORECASE)
match = pattern.search(html)

if match:
    intervals_str = match.group(1)
    time_pattern = re.compile(r"t_from:\s*['\"](\d{1,2}:\d{2})['\"],\s*t_to:\s*['\"](\d{1,2}:\d{2})['\"]")
    matches = time_pattern.findall(intervals_str)
    
    parsed = []
    for f, t in matches:
        start = datetime.strptime(f, '%H:%M').time()
        end = datetime.strptime(t, '%H:%M').time()
        parsed.append((start, end))
    print("Parsed 259:", parsed)
else:
    print("Not found")

hdo_code = '246'
pattern = re.compile(rf"code:\s*['\"]?{hdo_code}['\"]?,\s*intervals:\s*\[(.*?)\]", re.DOTALL | re.IGNORECASE)
match = pattern.search(html)

if match:
    intervals_str = match.group(1)
    time_pattern = re.compile(r"t_from:\s*['\"](\d{1,2}:\d{2})['\"],\s*t_to:\s*['\"](\d{1,2}:\d{2})['\"]")
    matches = time_pattern.findall(intervals_str)
    
    parsed = []
    for f, t in matches:
        start = datetime.strptime(f, '%H:%M').time()
        end = datetime.strptime(t, '%H:%M').time()
        parsed.append((start, end))
    print("Parsed 246:", parsed)
else:
    print("Not found")

