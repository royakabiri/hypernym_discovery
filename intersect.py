import sys

def intersection(lst1, lst2):
     lst3 = [value for value in lst1 if value in lst2]
     return lst3



def main (goldFile, crimFile, gcnFile):
     with open (goldFile) as gold_file, open (crimFile) as crim_file, open (gcnFile) as gcn_file:
         goldList = []
         for line in gold_file:
             hypernyms = line.strip().split("\t")
             for hypernym in hypernyms:
                 goldList.append(hypernym)

         crimList = []
         for line in crim_file:
             hypernyms = line.strip().split("\t")
             for hypernym in hypernyms:
                 crimList.append(hypernym)
 
         gcnList = []
         for line in gcn_file:
             hypernyms = line.strip().split("\t")
             for hypernym in hypernyms:
                 gcnList.append(hypernym)

         crim_results = intersection(goldList, crimList)
         gcn_results = intersection(goldList, gcnList)
         intersection_results = intersection(crim_results, gcn_results)

         print(len(crim_results))
         print(len(gcn_results))
         print(len(intersection_results))

if __name__ == '__main__':
    goldFile = sys.argv[1]
    crimFile = sys.argv[2]
    gcnFile = sys.argv[3]
    main(goldFile, crimFile, gcnFile)
