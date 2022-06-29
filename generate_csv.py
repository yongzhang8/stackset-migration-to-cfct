import os
import csv
import re


stacksets = {}

for file in os.listdir('.'):
    if file.startswith('report_stackset_'):
        stack_set_name = re.match("report_stackset_(.*)-(drift|noncurrent|parameter|extras)\.txt",file)[1]
        report_type = re.match("report_stackset_(.*)-(drift|noncurrent|parameter|extras)\.txt",file)[2]
        
        num_lines = sum(1 if line.startswith('arn') else 0 for line in open(file))
        if not stack_set_name in stacksets:
            stacksets[stack_set_name] = {}
        stacksets[stack_set_name][report_type] = num_lines


with open('summary.csv', 'w') as outfile:
    fieldnames = ['name', 'drifts', 'non_currents', 'parameters', 'extras_instances']
    writer = csv.DictWriter(outfile, fieldnames=fieldnames)

    writer.writeheader()
    # writer.writerow({'first_name': 'Baked', 'last_name': 'Beans'})
    # writer.writerow({'first_name': 'Lovely', 'last_name': 'Spam'})
    # writer.writerow({'first_name': 'Wonderful', 'last_name': 'Spam'})
    for key, value in stacksets.items():
        writer.writerow(dict(
            name=key,
            drifts=value.get('drift'),
            non_currents=value.get('noncurrent'),
            parameters=value.get('parameter'),
            extras_instances=value.get('extras')
        ))